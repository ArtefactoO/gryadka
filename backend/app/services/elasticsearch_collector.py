"""
Service for collecting Elasticsearch cluster state and utilization.
Reuses a persistent Elasticsearch client per target across collect runs (same worker process).
"""
import hashlib
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from elasticsearch import Elasticsearch
from urllib3.exceptions import ProtocolError
from urllib.parse import urlparse

from app.config import get_app_config
from app.services.node_exporter_metrics import (
    elasticsearch_node_host_candidates,
    enrich_nodes_with_node_exporter,
)

# Connection/transport errors (elasticsearch wraps urllib3 ProtocolError)
try:
    from elasticsearch.exceptions import ConnectionError as ESConnectionError
except ImportError:
    ESConnectionError = type("ESConnectionError", (Exception,), {})

logger = logging.getLogger(__name__)


def _host_without_port(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = parsed.hostname if parsed.scheme and parsed.hostname else raw
    if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        host = host.rsplit(":", 1)[0]
    return host


def _short_hostname(host: str) -> str:
    return _host_without_port(host).split(".", 1)[0]


def _configured_node_aliases(nodes: List[Any]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for node in nodes:
        raw = node.get("host", node) if isinstance(node, dict) else node
        host = _host_without_port(raw)
        short = _short_hostname(host)
        if host and short:
            aliases[short] = host
    return aliases


def _public_node_name(node_name: str, configured_aliases: Dict[str, str]) -> str:
    short = _short_hostname(node_name)
    if short in configured_aliases:
        return configured_aliases[short]
    return node_name


class ElasticsearchCollector:
    """Collect Elasticsearch cluster health and node utilization. Keeps one client per target."""

    _client_cache: Dict[str, Tuple[Elasticsearch, str]] = {}
    _cache_lock = threading.Lock()
    _last_counter: Dict[Tuple[Any, ...], Tuple[float, float]] = {}

    def _config_key(self, hosts: List[str], username: Optional[str], password: Optional[str], verify_tls: bool) -> str:
        raw = "|".join(hosts) + f"|{username}|{password}|{verify_tls}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_or_create_client(
        self, target_name: str, es_config: Dict[str, Any], config_key: str
    ) -> Optional[Elasticsearch]:
        with self._cache_lock:
            entry = self._client_cache.get(target_name)
            if entry:
                client, cached_key = entry
                if cached_key == config_key:
                    return client
                try:
                    client.close()
                except Exception:
                    pass
                del self._client_cache[target_name]
        return Elasticsearch(**es_config)

    def _cache_client(self, target_name: str, client: Elasticsearch, config_key: str) -> None:
        with self._cache_lock:
            self._client_cache[target_name] = (client, config_key)

    def _invalidate_client(self, target_name: str) -> None:
        with self._cache_lock:
            entry = self._client_cache.pop(target_name, None)
            if entry:
                try:
                    entry[0].close()
                except Exception:
                    pass

    def _counter_rate(self, key: Tuple[Any, ...], value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        now_ts = datetime.now(timezone.utc).timestamp()
        previous = self._last_counter.get(key)
        self._last_counter[key] = (float(value), now_ts)
        if not previous:
            return None
        previous_value, previous_ts = previous
        elapsed = now_ts - previous_ts
        if elapsed <= 0 or value < previous_value:
            return None
        return round((float(value) - previous_value) / elapsed, 2)

    @staticmethod
    def _sum_node_metric(nodes: List[Dict[str, Any]], key: str) -> Optional[float]:
        values = [node.get(key) for node in nodes if node.get(key) is not None]
        if not values:
            return None
        return round(sum(float(value) for value in values), 2)

    def collect_target_data(self, target_name: str) -> Optional[Dict[str, Any]]:
        """Collect cluster state and utilization for an Elasticsearch target."""
        current_config = get_app_config()
        if not current_config:
            logger.error("App config not initialized")
            return None

        target_config = current_config.get_elasticsearch_target_by_name(target_name)
        if not target_config:
            logger.warning("Elasticsearch target %s not found in config", target_name)
            return None

        options = target_config.options or {}
        nodes = target_config.nodes or []
        node_aliases = _configured_node_aliases(nodes)
        url = options.get("url") or options.get("hosts")
        if not url and nodes:
            scheme = options.get("scheme", "http")
            port = options.get("port", 9200)
            def _node_url(n: Any) -> str:
                h = n.get("host", n) if isinstance(n, dict) else n
                h = str(h).strip()
                if "://" in h:
                    return h  # full URL
                if ":" in h:
                    return f"{scheme}://{h}"  # host:port
                return f"{scheme}://{h}:{port}"
            url = [_node_url(n) for n in nodes]
        if not url:
            logger.error("Elasticsearch target %s: url or nodes not configured", target_name)
            return None

        if isinstance(url, str):
            url = [url]
        username = options.get("username") or target_config.username
        password = options.get("password") or target_config.password
        timeout_s = float(options.get("timeoutS", 10) or 10)
        verify_tls = options.get("verifyTls", True) if "verifyTls" in options else True

        hosts = [h if isinstance(h, str) else str(h) for h in url]
        es_config: Dict[str, Any] = {
            "hosts": hosts,
            "request_timeout": int(timeout_s),
        }
        if username and password:
            es_config["basic_auth"] = (username, password)
        if not verify_tls:
            es_config["verify_certs"] = False
            es_config["ssl_show_warn"] = False

        config_key = self._config_key(hosts, username, password, verify_tls)
        es = self._get_or_create_client(target_name, es_config, config_key)

        try:
            health = es.cluster.health()
            nodes_stats = es.nodes.stats(metric="indices,os,jvm,fs,thread_pool")
            nodes_info: Dict[str, Any] = {}
            try:
                info_response = es.nodes.info()
                nodes_info = info_response.get("nodes", {}) if isinstance(info_response, dict) else {}
            except Exception:
                nodes_info = {}
            # Version: prefer cluster info (GET /) since nodes.stats(metric=...) may omit version
            cluster_version: Optional[str] = None
            try:
                info = es.info()
                ver_obj = info.get("version") if isinstance(info.get("version"), dict) else {}
                num = ver_obj.get("number")
                if num:
                    cluster_version = str(num).strip()
            except Exception:
                pass
            if not cluster_version:
                for _nid, node_info in nodes_stats.get("nodes", {}).items():
                    ver = node_info.get("version")
                    if isinstance(ver, str) and ver:
                        cluster_version = ver.strip()
                        break
                    if isinstance(ver, dict) and ver.get("number"):
                        cluster_version = str(ver["number"]).strip()
                        break

            master_node_id: Optional[str] = None
            try:
                state = es.cluster.state(metric="master_node")
                master_node_id = state.get("master_node")
            except Exception:
                pass

            shards_by_node: Dict[str, int] = {}
            try:
                for shard in es.cat.shards(format="json", h="node,state"):
                    node_name = str(shard.get("node") or "").strip()
                    if node_name:
                        shards_by_node[node_name] = shards_by_node.get(node_name, 0) + 1
            except Exception:
                shards_by_node = {}
            short_shards_by_node = {
                _short_hostname(node_name): count
                for node_name, count in shards_by_node.items()
                if _short_hostname(node_name)
            }

            nodes_data: List[Dict[str, Any]] = []
            total_docs = 0
            total_store_size = 0
            for node_id, node_info in nodes_stats.get("nodes", {}).items():
                node_identity = nodes_info.get(node_id, {}) if isinstance(nodes_info, dict) else {}
                raw_node_name = str(node_info.get("name", node_id))
                public_node_name = _public_node_name(raw_node_name, node_aliases)
                node_host = node_identity.get("host") if isinstance(node_identity, dict) else None
                node_ip = node_identity.get("ip") if isinstance(node_identity, dict) else None
                transport_address = node_identity.get("transport_address") if isinstance(node_identity, dict) else None
                indices = node_info.get("indices", {})
                docs = indices.get("docs", {}).get("count", 0)
                store_size = indices.get("store", {}).get("size_in_bytes", 0)
                indexing = indices.get("indexing", {}) if isinstance(indices.get("indexing"), dict) else {}
                search = indices.get("search", {}) if isinstance(indices.get("search"), dict) else {}
                total_docs += docs
                total_store_size += store_size

                os_data = node_info.get("os", {})
                mem_data = os_data.get("mem", {})
                cpu_data = os_data.get("cpu", {})
                jvm_data = node_info.get("jvm", {}).get("mem", {})
                fs_data = node_info.get("fs") or {}
                fs_io_total = (((fs_data.get("io_stats") or {}).get("total") or {}) if isinstance(fs_data, dict) else {})
                fs_total = (isinstance(fs_data, dict) and fs_data.get("total")) or {}
                total_bytes = (fs_total.get("total_in_bytes") if isinstance(fs_total, dict) else None) or fs_data.get("total_in_bytes")
                free_bytes = (fs_total.get("free_in_bytes") if isinstance(fs_total, dict) else None) or fs_data.get("free_in_bytes")
                disk_used_percent: Optional[float] = None
                if total_bytes is not None and free_bytes is not None and int(total_bytes) > 0:
                    used = int(total_bytes) - int(free_bytes)
                    disk_used_percent = round(100.0 * used / int(total_bytes), 2)
                shard_candidates = [
                    raw_node_name,
                    public_node_name,
                ]
                short_shard_candidates = [
                    _short_hostname(raw_node_name),
                    _short_hostname(public_node_name),
                ]
                shards_count = next((shards_by_node[item] for item in shard_candidates if item in shards_by_node), None)
                if shards_count is None:
                    shards_count = next(
                        (short_shards_by_node[item] for item in short_shard_candidates if item in short_shards_by_node),
                        None,
                    )
                thread_pool = node_info.get("thread_pool", {}) if isinstance(node_info.get("thread_pool"), dict) else {}
                search_pool = thread_pool.get("search", {}) if isinstance(thread_pool.get("search"), dict) else {}
                write_pool = thread_pool.get("write", {}) if isinstance(thread_pool.get("write"), dict) else {}
                fs_read_kb = fs_io_total.get("read_kilobytes") if isinstance(fs_io_total, dict) else None
                fs_write_kb = fs_io_total.get("write_kilobytes") if isinstance(fs_io_total, dict) else None

                role = "MASTER" if (master_node_id and node_id == master_node_id) else "DATA"
                nodes_data.append({
                    "node_id": node_id,
                    "name": public_node_name,
                    "internal_name": raw_node_name,
                    "host": node_host,
                    "ip": node_ip,
                    "transport_address": transport_address,
                    "role": role,
                    "shards_count": shards_count,
                    "docs_count": docs,
                    "store_size_bytes": store_size,
                    "indexing_rate": self._counter_rate(
                        (target_name, node_id, "indexing_rate"),
                        indexing.get("index_total"),
                    ),
                    "search_rate": self._counter_rate(
                        (target_name, node_id, "search_rate"),
                        search.get("query_total"),
                    ),
                    "fs_read_bytes_rate": self._counter_rate(
                        (target_name, node_id, "fs_read_bytes_rate"),
                        float(fs_read_kb) * 1024 if fs_read_kb is not None else None,
                    ),
                    "fs_write_bytes_rate": self._counter_rate(
                        (target_name, node_id, "fs_write_bytes_rate"),
                        float(fs_write_kb) * 1024 if fs_write_kb is not None else None,
                    ),
                    "search_rejected": search_pool.get("rejected"),
                    "write_rejected": write_pool.get("rejected"),
                    "heap_used_percent": jvm_data.get("heap_used_percent"),
                    "heap_used_bytes": jvm_data.get("heap_used_in_bytes"),
                    "heap_max_bytes": jvm_data.get("heap_max_in_bytes"),
                    "cpu_percent": cpu_data.get("percent"),
                    "mem_percent": mem_data.get("used_percent"),
                    "disk_used_percent": disk_used_percent,
                })

            nodes_data = enrich_nodes_with_node_exporter(
                nodes_data,
                elasticsearch_node_host_candidates,
            )

            overview = {
                "status": health.get("status", "unknown"),
                "number_of_nodes": health.get("number_of_nodes", 0),
                "number_of_data_nodes": health.get("number_of_data_nodes", 0),
                "active_primary_shards": health.get("active_primary_shards", 0),
                "active_shards": health.get("active_shards", 0),
                "relocating_shards": health.get("relocating_shards", 0),
                "initializing_shards": health.get("initializing_shards", 0),
                "unassigned_shards": health.get("unassigned_shards", 0),
                "delayed_unassigned_shards": health.get("delayed_unassigned_shards", 0),
                "total_docs": total_docs,
                "total_store_size_bytes": total_store_size,
                "cluster_name": health.get("cluster_name", ""),
                "version": cluster_version,
            }
            indexing_rate = self._sum_node_metric(nodes_data, "indexing_rate")
            search_rate = self._sum_node_metric(nodes_data, "search_rate")
            fs_read_bytes_rate = self._sum_node_metric(nodes_data, "fs_read_bytes_rate")
            fs_write_bytes_rate = self._sum_node_metric(nodes_data, "fs_write_bytes_rate")
            if indexing_rate is not None:
                overview["indexing_rate"] = indexing_rate
            if search_rate is not None:
                overview["search_rate"] = search_rate
            if fs_read_bytes_rate is not None:
                overview["fs_read_bytes_rate"] = fs_read_bytes_rate
            if fs_write_bytes_rate is not None:
                overview["fs_write_bytes_rate"] = fs_write_bytes_rate

            self._cache_client(target_name, es, config_key)
            return {
                "name": target_name,
                "overview": overview,
                "nodes": nodes_data,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

        except (ProtocolError, ESConnectionError) as e:
            self._invalidate_client(target_name)
            cause = getattr(e, "__cause__", None) or e
            logger.error(
                "Elasticsearch target %s: connection failed (remote closed). "
                "If the cluster uses HTTPS, add under the target: options: { scheme: https, port: 9243 }; "
                "for self-signed certs add verifyTls: false. Error: %s",
                target_name,
                cause,
            )
            return None
        except Exception as e:
            self._invalidate_client(target_name)
            logger.error("Error collecting Elasticsearch data for target %s: %s", target_name, e)
            return None
