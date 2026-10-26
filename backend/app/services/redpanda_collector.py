"""
Service for collecting Redpanda (Kafka API) consumer group lags.
Reuses KafkaAdminClient per target across collect runs; KafkaConsumer is created per run for end_offsets.
Fetches Redpanda version, disk, CPU and memory from Admin API (cluster_view + public_metrics on port 9644).
"""
import json
import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kafka import KafkaAdminClient, KafkaConsumer

from app.config import get_app_config
from app.services.kafka_protocol import KafkaOffsetReader, client_options
from app.monitor_logging import log_monitor_request
from app.services.infrastructure_metrics import enrich_node_resources
from app.services.http_client import open_http


logger = logging.getLogger(__name__)

REDPANDA_ADMIN_API_PORT = 9644
REDPANDA_CLUSTER_VIEW_PATH = "/v1/cluster_view"
REDPANDA_HEALTH_OVERVIEW_PATH = "/v1/cluster/health_overview"
REDPANDA_PUBLIC_METRICS_PATH = "/public_metrics"
ADMIN_REQUEST_TIMEOUT_S = 5
# Prometheus text format: metric_name{label="val"} value [timestamp]
PROMETHEUS_LINE_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9_]+)(?:\{([^}]*)\})?\s+([\d.eE+-]+)(?:\s+\d+)?\s*$')
PROMETHEUS_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


class RedpandaCollector:
    """Collect consumer group lags from Redpanda via Kafka Admin API. Keeps admin client per target."""

    _admin_cache: Dict[str, KafkaAdminClient] = {}
    _cache_lock = threading.Lock()
    _last_cpu_busy: Dict[str, Tuple[float, float]] = {}  # target_name -> (cpu_busy_seconds, unix_ts)
    _last_cpu_busy_per_broker: Dict[Tuple[str, int], Tuple[float, float]] = {}  # (target_name, node_id) -> (cpu_busy, unix_ts)
    _last_counter: Dict[Tuple[Any, ...], Tuple[float, float]] = {}

    def _get_or_create_admin(self, target_name: str, common_config: Dict[str, Any]) -> KafkaAdminClient:
        with self._cache_lock:
            if target_name in self._admin_cache:
                return self._admin_cache[target_name]
        client = KafkaAdminClient(**common_config)
        with self._cache_lock:
            self._admin_cache[target_name] = client
        return client

    def _invalidate_admin(self, target_name: str) -> None:
        with self._cache_lock:
            admin = self._admin_cache.pop(target_name, None)
            if admin:
                try:
                    admin.close()
                except Exception:
                    pass

    def _fetch_cluster_view(
        self, bootstrap_servers: List[str], options: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[int]]:
        """Fetch GET /v1/cluster_view from first available broker. Returns (data, base_url_host, admin_port)."""
        admin_port = int(options.get("adminApiPort", REDPANDA_ADMIN_API_PORT))
        for server in bootstrap_servers:
            host = server.split(":")[0].strip()
            if not host:
                continue
            url = f"http://{host}:{admin_port}{REDPANDA_CLUSTER_VIEW_PATH}"
            try:
                req = urllib.request.Request(url)
                with open_http(req, timeout=ADMIN_REQUEST_TIMEOUT_S) as resp:
                    if resp.status != 200:
                        continue
                    data = json.loads(resp.read().decode())
                    return (data, host, admin_port)
            except Exception as e:
                log_monitor_request(logger, "redpanda", "cluster_view_failed", endpoint=url, error=str(e))
                continue
        return (None, None, None)

    def _fetch_health_overview(self, host: str, port: int) -> Optional[Dict[str, Any]]:
        """Fetch GET /v1/cluster/health_overview from broker."""
        url = f"http://{host}:{port}{REDPANDA_HEALTH_OVERVIEW_PATH}"
        try:
            req = urllib.request.Request(url)
            with open_http(req, timeout=ADMIN_REQUEST_TIMEOUT_S) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode())
        except Exception as e:
            log_monitor_request(logger, "redpanda", "health_overview_failed", endpoint=url, error=str(e))
            return None

    @staticmethod
    def _parse_prometheus_labels(raw: Optional[str]) -> Dict[str, str]:
        if not raw:
            return {}
        return {match.group(1): match.group(2).replace('\\"', '"') for match in PROMETHEUS_LABEL_RE.finditer(raw)}

    @classmethod
    def _parse_prometheus_metrics(cls, text: str) -> Dict[str, float]:
        """Parse Prometheus text format; return metric name -> sum of values (across labels)."""
        result: Dict[str, float] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = PROMETHEUS_LINE_RE.match(line)
            if not m:
                continue
            name, labels_raw, val_str = m.group(1), m.group(2), m.group(3)
            try:
                val = float(val_str)
                result[name] = result.get(name, 0) + val
                labels = cls._parse_prometheus_labels(labels_raw)
                request = labels.get("redpanda_request")
                if name == "redpanda_kafka_request_bytes_total" and request:
                    key = f"{name}:{request}"
                    result[key] = result.get(key, 0) + val
            except ValueError:
                continue
        return result

    def _counter_rate(self, key: Tuple[Any, ...], value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        now_ts = time.time()
        previous = self._last_counter.get(key)
        self._last_counter[key] = (float(value), now_ts)
        if not previous:
            return None
        previous_value, previous_ts = previous
        elapsed = now_ts - previous_ts
        if elapsed <= 0 or value < previous_value:
            return None
        return round((float(value) - previous_value) / elapsed, 2)

    def _add_metric_rates(self, target: Dict[str, Any], metrics: Dict[str, float], rate_key_prefix: Tuple[Any, ...]) -> None:
        rate_specs = {
            "write_bytes_rate": "redpanda_kafka_request_bytes_total:produce",
            "read_bytes_rate": "redpanda_kafka_request_bytes_total:consume",
            "records_produced_rate": "redpanda_kafka_records_produced_total",
            "records_fetched_rate": "redpanda_kafka_records_fetched_total",
            "disk_read_ops_rate": "redpanda_io_queue_total_read_ops",
            "disk_write_ops_rate": "redpanda_io_queue_total_write_ops",
        }
        for output_key, metric_name in rate_specs.items():
            target[output_key] = self._counter_rate((*rate_key_prefix, output_key), metrics.get(metric_name))

        moving_from = metrics.get("redpanda_cluster_partition_moving_from_node")
        moving_to = metrics.get("redpanda_cluster_partition_moving_to_node")
        under_replicated = metrics.get("redpanda_kafka_under_replicated_replicas")
        target["partitions_moving_from"] = int(moving_from) if moving_from is not None else None
        target["partitions_moving_to"] = int(moving_to) if moving_to is not None else None
        target["under_replicated_replicas"] = int(under_replicated) if under_replicated is not None else None

    @staticmethod
    def _aggregate_broker_metrics(brokers: List[Dict[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key in (
            "read_bytes_rate",
            "write_bytes_rate",
            "records_fetched_rate",
            "records_produced_rate",
            "disk_read_ops_rate",
            "disk_write_ops_rate",
            "partitions_moving_from",
            "partitions_moving_to",
            "under_replicated_replicas",
        ):
            values = [b.get(key) for b in brokers if b.get(key) is not None]
            if values:
                result[key] = round(sum(float(v) for v in values), 2)
        return result

    def _fetch_public_metrics(self, host: str, port: int) -> Optional[Dict[str, float]]:
        """GET /public_metrics from broker; return parsed metric name -> sum of values."""
        url = f"http://{host}:{port}{REDPANDA_PUBLIC_METRICS_PATH}"
        try:
            req = urllib.request.Request(url)
            with open_http(req, timeout=ADMIN_REQUEST_TIMEOUT_S) as resp:
                if resp.status != 200:
                    return None
                return self._parse_prometheus_metrics(resp.read().decode())
        except Exception as e:
            log_monitor_request(logger, "redpanda", "public_metrics_failed", endpoint=url, error=str(e))
            return None

    @staticmethod
    def _normalize_redpanda_version(raw: str) -> str:
        """Return short version like '24.2.27' from 'v24.2.27 - 754dc2c...' or similar."""
        if not raw:
            return raw
        # Take semver part (e.g. v24.2.27 or 24.2.27), drop git hash after ' - '
        part = raw.split(" - ")[0].strip().lstrip("v")
        m = re.match(r"^(\d+\.\d+\.\d+)", part)
        return m.group(1) if m else part

    def _resource_usage_from_cluster_view_and_metrics(
        self,
        target_name: str,
        cluster_view: Dict[str, Any],
        metrics: Optional[Dict[str, float]],
        first_broker_host: Optional[str],
        admin_port: Optional[int],
    ) -> Dict[str, Any]:
        """Build overview extras: version, disk_*, memory_*, cpu_* from cluster_view and optional public_metrics."""
        out: Dict[str, Any] = {}
        brokers = cluster_view.get("brokers") or []
        if brokers:
            ver = brokers[0].get("version") if brokers else None
            if ver and isinstance(ver, str):
                out["version"] = self._normalize_redpanda_version(ver.strip())
            num_cores = brokers[0].get("num_cores")
            if num_cores is not None:
                out["num_cores"] = int(num_cores)
            disk_free_total = 0
            disk_total_total = 0
            for b in brokers:
                for ds in b.get("disk_space") or []:
                    disk_free_total += int(ds.get("free") or 0)
                    disk_total_total += int(ds.get("total") or 0)
            if disk_total_total > 0:
                out["disk_free_bytes"] = disk_free_total
                out["disk_total_bytes"] = disk_total_total
                out["disk_used_percent"] = round(100.0 * (1.0 - disk_free_total / disk_total_total), 2)
        if metrics:
            mem_alloc = metrics.get("redpanda_memory_allocated_memory")
            mem_avail = metrics.get("redpanda_memory_available_memory")
            if mem_alloc is not None and mem_avail is not None and (mem_alloc + mem_avail) > 0:
                out["memory_allocated_bytes"] = int(mem_alloc)
                out["memory_available_bytes"] = int(mem_avail)
                out["memory_used_percent"] = round(100.0 * mem_alloc / (mem_alloc + mem_avail), 2)
            disk_f = metrics.get("redpanda_storage_disk_free_bytes")
            disk_t = metrics.get("redpanda_storage_disk_total_bytes")
            if disk_t is not None and disk_t > 0 and disk_f is not None:
                if "disk_free_bytes" not in out:
                    out["disk_free_bytes"] = int(disk_f)
                    out["disk_total_bytes"] = int(disk_t)
                out["disk_used_percent"] = round(100.0 * (1.0 - disk_f / disk_t), 2)
            cpu_busy = metrics.get("redpanda_cpu_busy_seconds_total")
            num_cores = out.get("num_cores") or 1
            if cpu_busy is not None:
                now_ts = time.time()
                prev = self._last_cpu_busy.get(target_name)
                self._last_cpu_busy[target_name] = (float(cpu_busy), now_ts)
                if prev is not None:
                    prev_busy, prev_ts = prev
                    delta_ts = now_ts - prev_ts
                    if delta_ts > 0 and num_cores > 0:
                        cpu_pct = 100.0 * (float(cpu_busy) - prev_busy) / delta_ts / num_cores
                        out["cpu_percent"] = round(max(0, min(100, cpu_pct)), 2)
            self._add_metric_rates(out, metrics, (target_name, "cluster_seed"))
        return out

    def _build_brokers_with_metrics(
        self,
        target_name: str,
        cluster_view: Dict[str, Any],
        admin_port: int,
        nodes_down: Optional[List[int]] = None,
        controller_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Build list of brokers with per-broker disk, memory, CPU from cluster_view and public_metrics per host."""
        result: List[Dict[str, Any]] = []
        brokers = cluster_view.get("brokers") or []
        down_node_ids = {int(node_id) for node_id in (nodes_down or [])}
        has_health_overview = nodes_down is not None
        for b in brokers:
            node_id = b.get("node_id")
            is_controller = controller_id is not None and node_id is not None and int(node_id) == int(controller_id)
            host = (b.get("internal_rpc_address") or "").strip()
            if not host:
                continue
            disk_free = 0
            disk_total = 0
            for ds in b.get("disk_space") or []:
                disk_free += int(ds.get("free") or 0)
                disk_total += int(ds.get("total") or 0)
            disk_used_pct = round(100.0 * (1.0 - disk_free / disk_total), 2) if disk_total > 0 else None
            ver = b.get("version")
            version_short = self._normalize_redpanda_version(ver.strip()) if ver and isinstance(ver, str) else None
            num_cores = int(b.get("num_cores") or 1)
            if has_health_overview and node_id is not None:
                status = "DOWN" if int(node_id) in down_node_ids else "UP"
            else:
                status = "UP" if b.get("is_alive", False) else "DOWN"
            broker_entry: Dict[str, Any] = {
                "node_id": node_id,
                "host": host,
                "version": version_short,
                "role": "controller" if is_controller else "broker",
                "num_cores": num_cores,
                "disk_free_bytes": disk_free if disk_total > 0 else None,
                "disk_total_bytes": disk_total if disk_total > 0 else None,
                "disk_used_percent": disk_used_pct,
                "status": status,
            }
            metrics = self._fetch_public_metrics(host, admin_port)
            if metrics:
                mem_alloc = metrics.get("redpanda_memory_allocated_memory")
                mem_avail = metrics.get("redpanda_memory_available_memory")
                if mem_alloc is not None and mem_avail is not None and (mem_alloc + mem_avail) > 0:
                    broker_entry["memory_allocated_bytes"] = int(mem_alloc)
                    broker_entry["memory_available_bytes"] = int(mem_avail)
                    broker_entry["memory_used_percent"] = round(100.0 * mem_alloc / (mem_alloc + mem_avail), 2)
                disk_f = metrics.get("redpanda_storage_disk_free_bytes")
                disk_t = metrics.get("redpanda_storage_disk_total_bytes")
                if disk_t and disk_t > 0 and disk_f is not None:
                    broker_entry["disk_free_bytes"] = int(disk_f)
                    broker_entry["disk_total_bytes"] = int(disk_t)
                    broker_entry["disk_used_percent"] = round(100.0 * (1.0 - disk_f / disk_t), 2)
                cpu_busy = metrics.get("redpanda_cpu_busy_seconds_total")
                if cpu_busy is not None and node_id is not None:
                    now_ts = time.time()
                    key = (target_name, int(node_id))
                    prev = self._last_cpu_busy_per_broker.get(key)
                    self._last_cpu_busy_per_broker[key] = (float(cpu_busy), now_ts)
                    if prev is not None:
                        prev_busy, prev_ts = prev
                        delta_ts = now_ts - prev_ts
                        if delta_ts > 0 and num_cores > 0:
                            cpu_pct = 100.0 * (float(cpu_busy) - prev_busy) / delta_ts / num_cores
                            broker_entry["cpu_percent"] = round(max(0, min(100, cpu_pct)), 2)
                if node_id is not None:
                    self._add_metric_rates(broker_entry, metrics, (target_name, "broker", int(node_id)))
            result.append(broker_entry)
        return result

    @staticmethod
    def _build_fallback_brokers(bootstrap_servers: List[str], nodes: List[Any]) -> List[Dict[str, Any]]:
        brokers: List[Dict[str, Any]] = []
        seen_hosts = set()

        def add_host(raw_host: Any) -> None:
            host = str(raw_host or "").strip()
            if not host:
                return
            if ":" in host:
                host = host.split(":", 1)[0].strip()
            if not host or host in seen_hosts:
                return
            seen_hosts.add(host)
            brokers.append({
                "node_id": len(brokers),
                "host": host,
                "name": host,
                "version": None,
                "role": None,
                "num_cores": None,
                "disk_free_bytes": None,
                "disk_total_bytes": None,
                "disk_used_percent": None,
                "status": None,
                "memory_allocated_bytes": None,
                "memory_available_bytes": None,
                "memory_used_percent": None,
                "cpu_percent": None,
                "read_bytes_rate": None,
                "write_bytes_rate": None,
                "records_fetched_rate": None,
                "records_produced_rate": None,
                "disk_read_ops_rate": None,
                "disk_write_ops_rate": None,
                "partitions_moving_from": None,
                "partitions_moving_to": None,
                "under_replicated_replicas": None,
            })

        for node in nodes:
            add_host(node.get("host", node) if isinstance(node, dict) else node)
        for server in bootstrap_servers:
            add_host(server)
        return brokers

    def collect_target_data(self, target_name: str) -> Optional[Dict[str, Any]]:
        """Collect consumer group lags for a Redpanda target."""
        current_config = get_app_config()
        if not current_config:
            logger.error("App config not initialized")
            return None

        target_config = current_config.get_redpanda_target_by_name(target_name)
        if not target_config:
            logger.warning("Redpanda target %s not found in config", target_name)
            return None

        options = target_config.options or {}
        nodes = target_config.nodes or []
        common_config = client_options(target_config, default_sasl_protocol='SASL_PLAINTEXT', default_sasl_mechanism='PLAIN')
        bootstrap_servers = common_config['bootstrap_servers']
        if not bootstrap_servers:
            return None

        consumer = None
        try:
            admin_client = self._get_or_create_admin(target_name, common_config)
            consumer = KafkaConsumer(
                **common_config,
                consumer_timeout_ms=1000,
                enable_auto_commit=False,
            )

            groups_raw = admin_client.list_consumer_groups()
            consumer_groups = [
                g[0] for g in groups_raw
                if isinstance(g, (list, tuple)) and len(g) >= 2 and (g[1] == "consumer" or g[1] == "")
            ]

            groups_data: List[Dict[str, Any]] = []
            total_lag = 0
            max_lag = 0
            groups_with_lag = 0

            offset_reader = KafkaOffsetReader(consumer)
            lag_complete = True
            for group_id in consumer_groups:
                try:
                    offsets = admin_client.list_consumer_group_offsets(group_id)
                    row = offset_reader.read(group_id, offsets)
                    row['state'] = 'unknown' if row['lag'] is None else 'active' if row['lag'] > 0 else 'ok'
                except Exception as exc:
                    logger.warning('Redpanda group metadata unavailable: target=%s error_type=%s', target_name, type(exc).__name__)
                    row = {'group_id': group_id, 'lag': None, 'partitions': [], 'state': 'unavailable'}
                if row['lag'] is None:
                    lag_complete = False
                else:
                    total_lag += row['lag']
                    max_lag = max(max_lag, row['lag'])
                    groups_with_lag += row['lag'] > 0
                groups_data.append(row)

            overview = {
                "total_groups": len(groups_data),
                "groups_with_lag": groups_with_lag,
                "total_lag": total_lag if lag_complete else None,
                "lag_complete": lag_complete,
                "max_group_lag": max_lag if lag_complete else None,
            }
            # kafka-python describe_cluster() returns a dict with controller_id, brokers, cluster_id
            controller_id = None
            try:
                cluster_metadata = admin_client.describe_cluster()
                if isinstance(cluster_metadata, dict):
                    raw = cluster_metadata.get("controller_id")
                    if raw is not None:
                        try:
                            controller_id = int(raw)
                        except (TypeError, ValueError):
                            pass
            except Exception as e:
                log_monitor_request(logger, "redpanda", "describe_cluster_failed", host=target_name, error=str(e))
            cluster_view, admin_host, admin_port = self._fetch_cluster_view(bootstrap_servers, options)
            metrics = None
            health_overview = None
            if admin_host is not None and admin_port is not None:
                metrics = self._fetch_public_metrics(admin_host, admin_port)
                health_overview = self._fetch_health_overview(admin_host, admin_port)
            brokers_with_metrics: List[Dict[str, Any]] = []
            if cluster_view:
                resources = self._resource_usage_from_cluster_view_and_metrics(
                    target_name, cluster_view, metrics, admin_host, admin_port
                )
                overview.update(resources)
                if health_overview and health_overview.get("controller_id") is not None:
                    try:
                        overview["controller_id"] = int(health_overview["controller_id"])
                    except (TypeError, ValueError):
                        overview["controller_id"] = controller_id
                else:
                    overview["controller_id"] = controller_id
                versions = [b.get("version") for b in (cluster_view.get("brokers") or []) if b.get("version")]
                if len(set(versions)) > 1:
                    overview["version_mismatch"] = True
                if health_overview:
                    overview["is_healthy"] = bool(health_overview.get("is_healthy", False))
                    overview["nodes_down"] = list(health_overview.get("nodes_down") or [])
                if admin_port is not None:
                    brokers_with_metrics = self._build_brokers_with_metrics(
                        target_name,
                        cluster_view,
                        admin_port,
                        list(health_overview.get("nodes_down") or []) if health_overview else None,
                        overview.get("controller_id"),
                    )
                    brokers_with_metrics = enrich_node_resources(brokers_with_metrics, target_config)
                    overview.update(self._aggregate_broker_metrics(brokers_with_metrics))
            else:
                fallback_brokers = self._build_fallback_brokers(bootstrap_servers, nodes)
                brokers_with_metrics = enrich_node_resources(fallback_brokers, target_config)

            return {
                "name": target_name,
                "overview": overview,
                "brokers": brokers_with_metrics,
                "consumer_groups": sorted(groups_data, key=lambda g: (g["lag"] is None, g["lag"] or 0), reverse=True),
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            self._invalidate_admin(target_name)
            logger.error("Error collecting Redpanda data for target %s: %s", target_name, e)
            return None
        finally:
            if consumer:
                try:
                    consumer.close()
                except Exception:
                    pass
