"""
Service for collecting Redis Sentinel-based topology and node metrics.
Reuses Redis client per (target_name, host, port) across collect runs.
"""
import logging
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from redis import Redis

from app.config import get_app_config
from app.services.endpoints import parse_host_port
from app.monitor_logging import log_collector_progress
from app.services.infrastructure_metrics import enrich_node_resources

logger = logging.getLogger(__name__)


class RedisCollector:
    @staticmethod
    def _is_sentinel_target(options: Dict[str, Any], nodes: List[Tuple[str, int]], sentinel_port: int) -> bool:
        configured_mode = str(options.get("mode", "")).strip().lower()
        if configured_mode == "sentinel":
            return True
        if configured_mode == "direct":
            return False
        if options.get("masterName"):
            return True
        return any(port == sentinel_port for _, port in nodes)

    """Collect Redis metrics via Sentinel and direct INFO commands. Keeps one client per node."""

    _client_cache: Dict[Tuple[str, str, int], Redis] = {}  # (target_name, host, port) -> Redis
    _cache_lock = threading.Lock()
    _target_modes: Dict[str, bool] = {}
    _last_counter: Dict[Tuple[str, str, int, str], Tuple[float, float]] = {}

    def _get_or_create_client(
        self,
        target_name: str,
        host: str,
        port: int,
        username: Optional[str],
        password: Optional[str],
        timeout_s: float,
    ) -> Redis:
        key = (target_name, host, port)
        with self._cache_lock:
            if key in self._client_cache:
                return self._client_cache[key]
        kwargs: Dict[str, Any] = {
            "host": host,
            "port": port,
            "socket_timeout": timeout_s,
            "socket_connect_timeout": timeout_s,
            "decode_responses": True,
        }
        if username:
            kwargs["username"] = username
        if password:
            kwargs["password"] = password
        client = Redis(**kwargs)
        with self._cache_lock:
            self._client_cache[key] = client
        return client

    def _invalidate_client(self, target_name: str, host: str, port: int) -> None:
        key = (target_name, host, port)
        with self._cache_lock:
            client = self._client_cache.pop(key, None)
            if client:
                try:
                    client.close()
                except Exception:
                    pass

    def _get_target_config(self, target_name: str):
        current_config = get_app_config()
        if not current_config:
            return None
        for target in current_config.redis_targets:
            if target.name == target_name:
                return target
        return None

    def _resolve_target_mode(
        self,
        target_name: str,
        options: Dict[str, Any],
        nodes: List[Tuple[str, int]],
        sentinel_port: int,
        timeout_s: float,
        username: Optional[str],
        password: Optional[str],
    ) -> bool:
        configured_mode = str(options.get("mode", "")).strip().lower()
        if configured_mode == "sentinel":
            return True
        if configured_mode == "direct":
            return False
        if options.get("masterName"):
            with self._cache_lock:
                self._target_modes[target_name] = True
            return True

        with self._cache_lock:
            cached_mode = self._target_modes.get(target_name)
        if cached_mode is not None:
            return cached_mode

        if any(port == sentinel_port for _, port in nodes):
            with self._cache_lock:
                self._target_modes[target_name] = True
            return True

        detected_mode = False
        for host, port in nodes:
            try:
                client = self._get_or_create_client(
                    target_name,
                    host,
                    port,
                    username,
                    password,
                    timeout_s,
                )
                role = str(client.info("replication").get("role", "")).lower()
                detected_mode = role == "sentinel"
                break
            except Exception:
                self._invalidate_client(target_name, host, port)
                continue

        with self._cache_lock:
            self._target_modes[target_name] = detected_mode
        return detected_mode

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _counter_rate(self, key: Tuple[str, str, int, str], value: Optional[float]) -> Optional[float]:
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

    def _runtime_metrics(self, target_name: str, host: str, port: int, info: Dict[str, Any]) -> Dict[str, Any]:
        metrics = {
            "input_kbps": self._to_float(info.get("instantaneous_input_kbps")),
            "output_kbps": self._to_float(info.get("instantaneous_output_kbps")),
            "blocked_clients": self._to_int(info.get("blocked_clients")),
            "used_memory_rss_bytes": self._to_int(info.get("used_memory_rss")),
            "used_memory_peak_bytes": self._to_int(info.get("used_memory_peak")),
            "mem_fragmentation_ratio": self._to_float(info.get("mem_fragmentation_ratio")),
            "evicted_keys_rate": self._counter_rate(
                (target_name, host, port, "evicted_keys"),
                self._to_float(info.get("evicted_keys")),
            ),
            "expired_keys_rate": self._counter_rate(
                (target_name, host, port, "expired_keys"),
                self._to_float(info.get("expired_keys")),
            ),
            "rejected_connections_rate": self._counter_rate(
                (target_name, host, port, "rejected_connections"),
                self._to_float(info.get("rejected_connections")),
            ),
            "keyspace_hits_rate": self._counter_rate(
                (target_name, host, port, "keyspace_hits"),
                self._to_float(info.get("keyspace_hits")),
            ),
            "keyspace_misses_rate": self._counter_rate(
                (target_name, host, port, "keyspace_misses"),
                self._to_float(info.get("keyspace_misses")),
            ),
        }
        return {key: value for key, value in metrics.items() if value is not None}

    @staticmethod
    def _parse_sentinel_row(raw_row: Any) -> Dict[str, str]:
        if isinstance(raw_row, dict):
            return {str(key): str(value) for key, value in raw_row.items()}
        if isinstance(raw_row, (list, tuple)):
            row = [str(item) for item in raw_row]
            result: Dict[str, str] = {}
            for index in range(0, len(row) - 1, 2):
                result[row[index]] = row[index + 1]
            return result
        return {}

    @staticmethod
    def _extract_host_port(target: Any, default_port: int) -> Tuple[str, int]:
        return parse_host_port(target, default_port)

        raw = str(target).strip()
        if not raw:
            return "", default_port
        if ":" in raw:
            host_part, port_part = raw.rsplit(":", 1)
            try:
                return host_part.strip(), int(port_part.strip())
            except Exception:
                return raw, default_port
        return raw, default_port

    def _safe_get_save_rules(self, client: Redis, node_label: str) -> Optional[str]:
        """Return CONFIG GET save value without breaking collection on ACL restrictions."""
        try:
            persistence_cfg = client.config_get("save")
            return str(persistence_cfg.get("save", "") or "").strip()
        except Exception as exc:
            logger.warning("Redis node %s: could not read CONFIG GET save: %s", node_label, exc)
            return None

    @staticmethod
    def _extract_keyspace(info: Dict[str, Any]) -> List[Dict[str, Any]]:
        databases: List[Dict[str, Any]] = []
        for key, value in info.items():
            if not str(key).startswith("db") or not isinstance(value, dict):
                continue
            databases.append(
                {
                    "name": str(key),
                    "keys": int(value.get("keys", 0) or 0),
                    "expires": int(value.get("expires", 0) or 0),
                    "avg_ttl": RedisCollector._to_int(value.get("avg_ttl")),
                }
            )
        databases.sort(key=lambda item: item["name"])
        return databases

    @staticmethod
    def _node_name(host: str, port: int) -> str:
        return f"{host}:{port}"

    @staticmethod
    def _build_host_alias_map(sentinel_nodes: List[Tuple[str, int]]) -> Dict[str, str]:
        """Map resolved IP -> configured hostname for prettier node labels in UI."""
        aliases: Dict[str, str] = {}
        for host, _port in sentinel_nodes:
            try:
                ip = socket.gethostbyname(host)
                if ip and ip not in aliases:
                    aliases[ip] = host
            except Exception:
                continue
        return aliases

    @staticmethod
    def _flags_has_problem(flags: Optional[str]) -> bool:
        if not flags:
            return False
        normalized = flags.lower()
        return any(flag in normalized for flag in ("s_down", "o_down", "disconnected"))

    def _read_sentinel_topology(
        self,
        sentinel_client: Redis,
        configured_master_name: Optional[str],
    ) -> Tuple[Optional[str], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        masters_raw = sentinel_client.execute_command("SENTINEL", "MASTERS")
        masters = [self._parse_sentinel_row(row) for row in (masters_raw or [])]
        if not masters:
            return None, {}, [], []

        if configured_master_name:
            selected_master = next((m for m in masters if m.get("name") == configured_master_name), masters[0])
        else:
            selected_master = masters[0]

        master_name = selected_master.get("name")
        if not master_name:
            return None, {}, [], []

        master_raw = sentinel_client.execute_command("SENTINEL", "MASTER", master_name)
        slaves_raw = sentinel_client.execute_command("SENTINEL", "SLAVES", master_name)
        sentinels_raw = sentinel_client.execute_command("SENTINEL", "SENTINELS", master_name)
        return (
            master_name,
            self._parse_sentinel_row(master_raw),
            [self._parse_sentinel_row(row) for row in (slaves_raw or [])],
            [self._parse_sentinel_row(row) for row in (sentinels_raw or [])],
        )

    def collect_target_data(self, target_name: str) -> Optional[Dict[str, Any]]:
        target_config = self._get_target_config(target_name)
        if not target_config:
            logger.warning("Redis target %s not found in config", target_name)
            return None

        options = target_config.options or {}
        timeout_s = float(options.get("timeoutS", 5.0) or 5.0)
        sentinel_port = int(options.get("sentinelPort", 26379) or 26379)
        redis_port = int(options.get("redisPort", 6379) or 6379)
        configured_master_name = options.get("masterName")

        configured_nodes = [
            self._extract_host_port(node, redis_port if options.get("mode") == "direct" else sentinel_port)
            for node in (target_config.nodes or [])
        ]
        configured_nodes = [(host, port) for host, port in configured_nodes if host]
        host_aliases = self._build_host_alias_map(configured_nodes)
        if not configured_nodes:
            logger.warning("Redis target %s has no configured Redis nodes", target_name)
            return None

        is_sentinel = self._resolve_target_mode(
            target_name,
            options,
            configured_nodes,
            sentinel_port,
            timeout_s,
            target_config.username,
            target_config.password,
        )
        if not is_sentinel:
            return self._collect_direct_redis(
                target_name, target_config, configured_nodes, timeout_s, host_aliases
            )

        nodes_payload: List[Dict[str, Any]] = []
        databases_payload: List[Dict[str, Any]] = []
        replicas_total = 0
        replicas_up = 0
        sentinels_up = 0
        master_offset: Optional[int] = None
        master_host: Optional[str] = None
        master_port: Optional[int] = None
        master_name: Optional[str] = None
        sentinel_peers_count = 0

        working_sentinel: Optional[Redis] = None
        working_sentinel_host: Optional[str] = None
        working_sentinel_port: Optional[int] = None

        for host, port in configured_nodes:
            try:
                client = self._get_or_create_client(
                    target_name, host, port,
                    target_config.username,
                    target_config.password,
                    timeout_s,
                )
                info = client.info()
                # Sentinel nodes don't support CONFIG commands, skip RDB config check
                sentinels_up += 1
                nodes_payload.append(
                    {
                        "name": self._node_name(host, port),
                        "host": host,
                        "port": port,
                        "kind": "SENTINEL",
                        "role": "SENTINEL",
                        "status": "UP",
                        "redis_version": str(info.get("redis_version")) if info.get("redis_version") else None,
                        "uptime_seconds": self._to_int(info.get("uptime_in_seconds")),
                        "connected_clients": self._to_int(info.get("connected_clients")),
                        "rdb_enabled": None,  # Sentinel doesn't support CONFIG GET
                        "rdb_bgsave_in_progress": bool(info.get("rdb_bgsave_in_progress", False)),
                        "rdb_last_bgsave_status": (
                            str(info.get("rdb_last_bgsave_status"))
                            if info.get("rdb_last_bgsave_status") is not None
                            else None
                        ),
                        "rdb_last_save_time": self._to_int(info.get("rdb_last_save_time")),
                    }
                )
                if working_sentinel is None:
                    working_sentinel = client
                    working_sentinel_host = host
                    working_sentinel_port = port
            except Exception as exc:
                self._invalidate_client(target_name, host, port)
                nodes_payload.append(
                    {
                        "name": self._node_name(host, port),
                        "host": host,
                        "port": port,
                        "kind": "SENTINEL",
                        "role": "SENTINEL",
                        "status": "UNREACHABLE",
                        "status_reason": str(exc),
                    }
                )

        # Fallback: if no Sentinel available, try direct Redis connection
        if not working_sentinel:
            log_collector_progress(logger, "redis", target_name, "fallback_direct")
            return self._collect_direct_redis(
                target_name, target_config, configured_nodes, timeout_s, host_aliases
            )

        master_info: Dict[str, Any] = {}
        replicas_info: List[Dict[str, Any]] = []
        sentinels_info: List[Dict[str, Any]] = []
        try:
            master_name, master_info, replicas_info, sentinels_info = self._read_sentinel_topology(
                working_sentinel, configured_master_name
            )
            sentinel_peers_count = len(sentinels_info)
        except Exception as exc:
            if working_sentinel_host is not None and working_sentinel_port is not None:
                self._invalidate_client(target_name, working_sentinel_host, working_sentinel_port)
            logger.warning(
                "Redis target %s: could not read topology from sentinel %s:%s: %s",
                target_name,
                working_sentinel_host,
                working_sentinel_port,
                exc,
            )

        if master_info:
            master_host = str(master_info.get("ip", "")).strip() or None
            master_port = self._to_int(master_info.get("port"))
            master_host_label = host_aliases.get(master_host, master_host) if master_host else None
        else:
            master_host_label = None

        master_runtime: Dict[str, Any] = {}
        if master_host and master_port:
            try:
                master_client = self._get_or_create_client(
                    target_name, master_host, master_port,
                    target_config.username,
                    target_config.password,
                    timeout_s,
                )
                master_payload = master_client.info()
                save_rules = self._safe_get_save_rules(master_client, f"{master_host}:{master_port}")
                rdb_enabled = None if save_rules is None else bool(save_rules)
                rdb_bgsave_in_progress = bool(master_payload.get("rdb_bgsave_in_progress", False))
                rdb_last_bgsave_status = (
                    str(master_payload.get("rdb_last_bgsave_status"))
                    if master_payload.get("rdb_last_bgsave_status") is not None
                    else None
                )
                rdb_last_save_time = self._to_int(master_payload.get("rdb_last_save_time"))
                master_offset = self._to_int(master_payload.get("master_repl_offset"))
                databases_payload = self._extract_keyspace(master_payload)
                master_runtime = self._runtime_metrics(target_name, master_host, master_port, master_payload)
                nodes_payload.append(
                    {
                        "name": self._node_name(master_host_label or master_host, master_port),
                        "host": master_host_label or master_host,
                        "port": master_port,
                        "kind": "REDIS",
                        "role": "MASTER",
                        "status": "UP",
                        "redis_version": str(master_payload.get("redis_version")) if master_payload.get("redis_version") else None,
                        "uptime_seconds": self._to_int(master_payload.get("uptime_in_seconds")),
                        "connected_clients": self._to_int(master_payload.get("connected_clients")),
                        "used_memory_bytes": self._to_int(master_payload.get("used_memory")),
                        "maxmemory_bytes": self._to_int(master_payload.get("maxmemory")),
                        "total_memory_bytes": self._to_int(master_payload.get("total_system_memory")),
                        "ops_per_sec": self._to_int(master_payload.get("instantaneous_ops_per_sec")),
                        "rdb_enabled": rdb_enabled,
                        "rdb_bgsave_in_progress": bool(master_payload.get("rdb_bgsave_in_progress", False)),
                        "rdb_last_bgsave_status": (
                            str(master_payload.get("rdb_last_bgsave_status"))
                            if master_payload.get("rdb_last_bgsave_status") is not None
                            else None
                        ),
                        "rdb_last_save_time": self._to_int(master_payload.get("rdb_last_save_time")),
                        "replication_offset": master_offset,
                        **master_runtime,
                    }
                )

                total_hits = self._to_int(master_payload.get("keyspace_hits")) or 0
                total_misses = self._to_int(master_payload.get("keyspace_misses")) or 0
                requests = total_hits + total_misses
                hit_rate = (float(total_hits) / float(requests)) if requests > 0 else None
                keys_total = sum(int(item.get("keys", 0) or 0) for item in databases_payload)
                connected_clients = self._to_int(master_payload.get("connected_clients")) or 0
                ops_per_sec = self._to_int(master_payload.get("instantaneous_ops_per_sec")) or 0
                used_memory = self._to_int(master_payload.get("used_memory")) or 0
            except Exception as exc:
                self._invalidate_client(target_name, master_host, master_port)
                logger.warning(
                    "Redis target %s: could not collect master info %s:%s: %s",
                    target_name,
                    master_host,
                    master_port,
                    exc,
                )
                nodes_payload.append(
                    {
                        "name": self._node_name(master_host, master_port),
                        "host": master_host,
                        "port": master_port,
                        "kind": "REDIS",
                        "role": "MASTER",
                        "status": "UNREACHABLE",
                        "status_reason": str(exc),
                    }
                )
                connected_clients = 0
                ops_per_sec = 0
                used_memory = 0
                hit_rate = None
                keys_total = 0
                rdb_enabled = None
                rdb_bgsave_in_progress = None
                rdb_last_bgsave_status = None
                rdb_last_save_time = None
        else:
            connected_clients = 0
            ops_per_sec = 0
            used_memory = 0
            hit_rate = None
            keys_total = 0
            rdb_enabled = None
            rdb_bgsave_in_progress = None
            rdb_last_bgsave_status = None
            rdb_last_save_time = None

        replicas_total = len(replicas_info)
        for replica in replicas_info:
            replica_host = str(replica.get("ip", "")).strip()
            replica_host_label = host_aliases.get(replica_host, replica_host) if replica_host else replica_host
            replica_port = self._to_int(replica.get("port")) or redis_port
            flags = replica.get("flags")
            sentinel_problem = self._flags_has_problem(flags)
            replica_status = "DOWN" if sentinel_problem else "UP"
            replica_reason = f"Sentinel flags: {flags}" if sentinel_problem else None
            replica_offset: Optional[int] = None
            master_link_status: Optional[str] = None
            if replica_status == "UP":
                replicas_up += 1

            if replica_host:
                try:
                    replica_client = self._get_or_create_client(
                        target_name, replica_host, replica_port,
                        target_config.username,
                        target_config.password,
                        timeout_s,
                    )
                    replica_payload = replica_client.info()
                    replica_save_rules = self._safe_get_save_rules(replica_client, f"{replica_host}:{replica_port}")
                    replica_offset = self._to_int(replica_payload.get("slave_repl_offset"))
                    replica_runtime = self._runtime_metrics(target_name, replica_host, replica_port, replica_payload)
                    master_link_status = (
                        str(replica_payload.get("master_link_status"))
                        if replica_payload.get("master_link_status")
                        else None
                    )
                    nodes_payload.append(
                        {
                            "name": self._node_name(replica_host_label or replica_host, replica_port),
                            "host": replica_host_label or replica_host,
                            "port": replica_port,
                            "kind": "REDIS",
                            "role": "REPLICA",
                            "status": "UP",
                            "redis_version": str(replica_payload.get("redis_version")) if replica_payload.get("redis_version") else None,
                            "uptime_seconds": self._to_int(replica_payload.get("uptime_in_seconds")),
                            "connected_clients": self._to_int(replica_payload.get("connected_clients")),
                            "used_memory_bytes": self._to_int(replica_payload.get("used_memory")),
                            "maxmemory_bytes": self._to_int(replica_payload.get("maxmemory")),
                            "total_memory_bytes": self._to_int(replica_payload.get("total_system_memory")),
                            "ops_per_sec": self._to_int(replica_payload.get("instantaneous_ops_per_sec")),
                            "rdb_enabled": (None if replica_save_rules is None else bool(replica_save_rules)),
                            "rdb_bgsave_in_progress": bool(replica_payload.get("rdb_bgsave_in_progress", False)),
                            "rdb_last_bgsave_status": (
                                str(replica_payload.get("rdb_last_bgsave_status"))
                                if replica_payload.get("rdb_last_bgsave_status") is not None
                                else None
                            ),
                            "rdb_last_save_time": self._to_int(replica_payload.get("rdb_last_save_time")),
                            "master_host": str(replica_payload.get("master_host")) if replica_payload.get("master_host") else None,
                            "master_port": self._to_int(replica_payload.get("master_port")),
                            "master_link_status": master_link_status,
                            "replication_offset": replica_offset,
                            "lag_bytes": (
                                max(0, (master_offset or 0) - (replica_offset or 0))
                                if master_offset is not None and replica_offset is not None
                                else None
                            ),
                            **replica_runtime,
                        }
                    )
                    continue
                except Exception as exc:
                    self._invalidate_client(target_name, replica_host, replica_port)
                    replica_status = "UNREACHABLE"
                    replica_reason = str(exc)
                    if replicas_up > 0:
                        replicas_up -= 1

            nodes_payload.append(
                {
                    "name": self._node_name(replica_host_label or replica_host or "unknown", replica_port),
                    "host": replica_host_label or replica_host or "unknown",
                    "port": replica_port,
                    "kind": "REDIS",
                    "role": "REPLICA",
                    "status": replica_status,
                    "status_reason": replica_reason,
                    "master_link_status": master_link_status,
                    "replication_offset": replica_offset,
                }
            )

        # Sort nodes for stable UI order: master, replicas, sentinels.
        role_rank = {"MASTER": 0, "REPLICA": 1, "SENTINEL": 2}
        nodes_payload.sort(key=lambda item: (role_rank.get(str(item.get("role")), 9), str(item.get("host", "")), int(item.get("port", 0))))
        nodes_payload = enrich_node_resources(nodes_payload, target_config)

        return {
            "name": target_name,
            "overview": {
                "mode": "sentinel",
                "master_name": master_name,
                "master_host": master_host_label or master_host,
                "master_port": master_port,
                "replicas_total": replicas_total,
                "replicas_up": replicas_up,
                "sentinels_total": max(len(configured_nodes), sentinel_peers_count + 1 if sentinel_peers_count else 0),
                "sentinels_up": sentinels_up,
                "connected_clients": connected_clients,
                "ops_per_sec": ops_per_sec,
                "used_memory_bytes": used_memory,
                "keys_total": keys_total,
                "keyspace_hit_rate": hit_rate,
                "rdb_enabled": rdb_enabled,
                "rdb_bgsave_in_progress": rdb_bgsave_in_progress,
                "rdb_last_bgsave_status": rdb_last_bgsave_status,
                "rdb_last_save_time": rdb_last_save_time,
                "input_kbps": master_runtime.get("input_kbps"),
                "output_kbps": master_runtime.get("output_kbps"),
                "blocked_clients": master_runtime.get("blocked_clients"),
                "evicted_keys_rate": master_runtime.get("evicted_keys_rate"),
                "expired_keys_rate": master_runtime.get("expired_keys_rate"),
                "rejected_connections_rate": master_runtime.get("rejected_connections_rate"),
                "keyspace_hits_rate": master_runtime.get("keyspace_hits_rate"),
                "keyspace_misses_rate": master_runtime.get("keyspace_misses_rate"),
                "mem_fragmentation_ratio": master_runtime.get("mem_fragmentation_ratio"),
            },
            "nodes": nodes_payload,
            "databases": databases_payload,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }

    def _collect_direct_redis(
        self,
        target_name: str,
        target_config: Any,
        configured_nodes: List[Tuple[str, int]],
        timeout_s: float,
        host_aliases: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        """Collect Redis data via direct connection (no Sentinel)."""
        log_collector_progress(logger, "redis", target_name, "direct_collect_start")

        nodes_payload: List[Dict[str, Any]] = []
        databases_payload: List[Dict[str, Any]] = []
        master_host: Optional[str] = None
        master_port: Optional[int] = None
        master_offset: Optional[int] = None
        replicas_total = 0
        replicas_up = 0

        # Try to connect to each configured node and determine role
        redis_nodes = [
            (host, port)
            for host, port in configured_nodes
            if host
        ]

        if not redis_nodes:
            logger.warning("Redis target %s: no Redis nodes configured", target_name)
            return None

        # Connect to all nodes and collect info
        node_info_map: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for host, port in redis_nodes:
            try:
                client = self._get_or_create_client(
                    target_name, host, port,
                    target_config.username,
                    target_config.password,
                    timeout_s,
                )
                info = client.info()
                role = info.get("role")
                node_info_map[(host, port)] = {
                    "host": host,
                    "port": port,
                    "info": info,
                    "client": client,
                    "role": role,
                }
            except Exception as exc:
                self._invalidate_client(target_name, host, port)
                logger.warning("Redis target %s: could not connect to %s:%s: %s", target_name, host, port, exc)
                nodes_payload.append(
                    {
                        "name": self._node_name(host, port),
                        "host": host,
                        "port": port,
                        "kind": "REDIS",
                        "role": "UNKNOWN",
                        "status": "UNREACHABLE",
                        "status_reason": str(exc),
                    }
                )

        if not node_info_map:
            logger.error("Redis target %s: no Redis nodes reachable", target_name)
            return None

        # Find master (role='master') or use first node if all are replicas
        master_node = None
        for (host, port), node_data in node_info_map.items():
            if node_data["role"] == "master":
                master_node = (host, port, node_data)
                break

        if not master_node:
            # No master found, use first node as master (standalone or all replicas)
            first_key = list(node_info_map.keys())[0]
            master_node = (first_key[0], first_key[1], node_info_map[first_key])
            log_collector_progress(
                logger,
                "redis",
                target_name,
                "fallback_first_node_as_master",
                host=master_node[0],
                port=master_node[1],
            )

        master_host, master_port, master_data = master_node
        master_host_label = host_aliases.get(master_host, master_host)
        master_info = master_data["info"]
        master_client = master_data["client"]

        # Collect master data
        master_runtime: Dict[str, Any] = {}
        try:
            save_rules = self._safe_get_save_rules(master_client, f"{master_host}:{master_port}")
            rdb_enabled = None if save_rules is None else bool(save_rules)
            rdb_bgsave_in_progress = bool(master_info.get("rdb_bgsave_in_progress", False))
            rdb_last_bgsave_status = (
                str(master_info.get("rdb_last_bgsave_status"))
                if master_info.get("rdb_last_bgsave_status") is not None
                else None
            )
            rdb_last_save_time = self._to_int(master_info.get("rdb_last_save_time"))
            master_offset = self._to_int(master_info.get("master_repl_offset"))
            databases_payload = self._extract_keyspace(master_info)
            master_runtime = self._runtime_metrics(target_name, master_host, master_port, master_info)

            nodes_payload.append(
                {
                    "name": self._node_name(master_host_label, master_port),
                    "host": master_host_label,
                    "port": master_port,
                    "kind": "REDIS",
                    "role": "MASTER",
                    "status": "UP",
                    "redis_version": str(master_info.get("redis_version")) if master_info.get("redis_version") else None,
                    "uptime_seconds": self._to_int(master_info.get("uptime_in_seconds")),
                    "connected_clients": self._to_int(master_info.get("connected_clients")),
                    "used_memory_bytes": self._to_int(master_info.get("used_memory")),
                    "maxmemory_bytes": self._to_int(master_info.get("maxmemory")),
                    "total_memory_bytes": self._to_int(master_info.get("total_system_memory")),
                    "ops_per_sec": self._to_int(master_info.get("instantaneous_ops_per_sec")),
                    "rdb_enabled": rdb_enabled,
                    "rdb_bgsave_in_progress": bool(master_info.get("rdb_bgsave_in_progress", False)),
                    "rdb_last_bgsave_status": (
                        str(master_info.get("rdb_last_bgsave_status"))
                        if master_info.get("rdb_last_bgsave_status") is not None
                        else None
                    ),
                    "rdb_last_save_time": self._to_int(master_info.get("rdb_last_save_time")),
                    "replication_offset": master_offset,
                    **master_runtime,
                }
            )

            total_hits = self._to_int(master_info.get("keyspace_hits")) or 0
            total_misses = self._to_int(master_info.get("keyspace_misses")) or 0
            requests = total_hits + total_misses
            hit_rate = (float(total_hits) / float(requests)) if requests > 0 else None
            keys_total = sum(int(item.get("keys", 0) or 0) for item in databases_payload)
            connected_clients = self._to_int(master_info.get("connected_clients")) or 0
            ops_per_sec = self._to_int(master_info.get("instantaneous_ops_per_sec")) or 0
            used_memory = self._to_int(master_info.get("used_memory")) or 0
        except Exception as exc:
            self._invalidate_client(target_name, master_host, master_port)
            logger.warning("Redis target %s: could not collect master info %s:%s: %s", target_name, master_host, master_port, exc)
            connected_clients = 0
            ops_per_sec = 0
            used_memory = 0
            hit_rate = None
            keys_total = 0
            rdb_enabled = None
            rdb_bgsave_in_progress = None
            rdb_last_bgsave_status = None
            rdb_last_save_time = None

        # Collect replica data
        for (host, port), node_data in node_info_map.items():
            if (host, port) == (master_host, master_port):
                continue  # Skip master

            node_info = node_data["info"]
            node_role = node_data["role"]
            host_label = host_aliases.get(host, host)

            if node_role == "slave" or node_role == "replica":
                replicas_total += 1
                try:
                    replica_client = node_data["client"]
                    replica_info = replica_client.info()
                    save_rules = self._safe_get_save_rules(replica_client, f"{host}:{port}")
                    rdb_enabled_replica = None if save_rules is None else bool(save_rules)
                    replica_offset = self._to_int(replica_info.get("slave_repl_offset"))
                    master_link_status = str(replica_info.get("master_link_status", "down")).upper()
                    lag_bytes = None
                    if master_offset and replica_offset:
                        lag_bytes = max(0, master_offset - replica_offset)
                    replica_runtime = self._runtime_metrics(target_name, host, port, replica_info)

                    nodes_payload.append(
                        {
                            "name": self._node_name(host_label, port),
                            "host": host_label,
                            "port": port,
                            "kind": "REDIS",
                            "role": "REPLICA",
                            "status": "UP" if master_link_status == "UP" else "DOWN",
                            "status_reason": None if master_link_status == "UP" else f"Master link: {master_link_status}",
                            "redis_version": str(replica_info.get("redis_version")) if replica_info.get("redis_version") else None,
                            "uptime_seconds": self._to_int(replica_info.get("uptime_in_seconds")),
                            "connected_clients": self._to_int(replica_info.get("connected_clients")),
                            "used_memory_bytes": self._to_int(replica_info.get("used_memory")),
                            "maxmemory_bytes": self._to_int(replica_info.get("maxmemory")),
                            "total_memory_bytes": self._to_int(replica_info.get("total_system_memory")),
                            "ops_per_sec": self._to_int(replica_info.get("instantaneous_ops_per_sec")),
                            "lag_bytes": lag_bytes,
                            "replication_offset": replica_offset,
                            "master_link_status": master_link_status,
                            "rdb_enabled": rdb_enabled_replica,
                            "rdb_bgsave_in_progress": bool(replica_info.get("rdb_bgsave_in_progress", False)),
                            "rdb_last_bgsave_status": (
                                str(replica_info.get("rdb_last_bgsave_status"))
                                if replica_info.get("rdb_last_bgsave_status") is not None
                                else None
                            ),
                            "rdb_last_save_time": self._to_int(replica_info.get("rdb_last_save_time")),
                            **replica_runtime,
                        }
                    )
                    if master_link_status == "UP":
                        replicas_up += 1
                except Exception as exc:
                    self._invalidate_client(target_name, host, port)
                    logger.warning("Redis target %s: could not collect replica info %s:%s: %s", target_name, host, port, exc)
                    nodes_payload.append(
                        {
                            "name": self._node_name(host_label, port),
                            "host": host_label,
                            "port": port,
                            "kind": "REDIS",
                            "role": "REPLICA",
                            "status": "UNREACHABLE",
                            "status_reason": str(exc),
                        }
                    )
            else:
                # Unknown role or standalone
                try:
                    node_info = node_data["info"]
                    save_rules = self._safe_get_save_rules(node_data["client"], f"{host}:{port}")
                    rdb_enabled_node = None if save_rules is None else bool(save_rules)
                    node_runtime = self._runtime_metrics(target_name, host, port, node_info)
                    nodes_payload.append(
                        {
                            "name": self._node_name(host_label, port),
                            "host": host_label,
                            "port": port,
                            "kind": "REDIS",
                            "role": "MASTER" if node_role == "master" else "UNKNOWN",
                            "status": "UP",
                            "redis_version": str(node_info.get("redis_version")) if node_info.get("redis_version") else None,
                            "uptime_seconds": self._to_int(node_info.get("uptime_in_seconds")),
                            "connected_clients": self._to_int(node_info.get("connected_clients")),
                            "used_memory_bytes": self._to_int(node_info.get("used_memory")),
                            "maxmemory_bytes": self._to_int(node_info.get("maxmemory")),
                            "total_memory_bytes": self._to_int(node_info.get("total_system_memory")),
                            "ops_per_sec": self._to_int(node_info.get("instantaneous_ops_per_sec")),
                            "rdb_enabled": rdb_enabled_node,
                            "rdb_bgsave_in_progress": bool(node_info.get("rdb_bgsave_in_progress", False)),
                            "rdb_last_bgsave_status": (
                                str(node_info.get("rdb_last_bgsave_status"))
                                if node_info.get("rdb_last_bgsave_status") is not None
                                else None
                            ),
                            "rdb_last_save_time": self._to_int(node_info.get("rdb_last_save_time")),
                            **node_runtime,
                        }
                    )
                except Exception as exc:
                    self._invalidate_client(target_name, host, port)
                    logger.warning("Redis target %s: could not collect node info %s:%s: %s", target_name, host, port, exc)

        # Sort nodes for stable UI order: master, replicas
        role_rank = {"MASTER": 0, "REPLICA": 1, "UNKNOWN": 2, "SENTINEL": 3}
        nodes_payload.sort(key=lambda item: (role_rank.get(str(item.get("role")), 9), str(item.get("host", "")), int(item.get("port", 0))))
        nodes_payload = enrich_node_resources(nodes_payload, target_config)

        return {
            "name": target_name,
            "overview": {
                "mode": "direct",
                "master_name": None,
                "master_host": master_host_label,
                "master_port": master_port,
                "replicas_total": replicas_total,
                "replicas_up": replicas_up,
                "sentinels_total": 0,
                "sentinels_up": 0,
                "connected_clients": connected_clients,
                "ops_per_sec": ops_per_sec,
                "used_memory_bytes": used_memory,
                "keys_total": keys_total,
                "keyspace_hit_rate": hit_rate,
                "rdb_enabled": rdb_enabled,
                "rdb_bgsave_in_progress": rdb_bgsave_in_progress,
                "rdb_last_bgsave_status": rdb_last_bgsave_status,
                "rdb_last_save_time": rdb_last_save_time,
                "input_kbps": master_runtime.get("input_kbps"),
                "output_kbps": master_runtime.get("output_kbps"),
                "blocked_clients": master_runtime.get("blocked_clients"),
                "evicted_keys_rate": master_runtime.get("evicted_keys_rate"),
                "expired_keys_rate": master_runtime.get("expired_keys_rate"),
                "rejected_connections_rate": master_runtime.get("rejected_connections_rate"),
                "keyspace_hits_rate": master_runtime.get("keyspace_hits_rate"),
                "keyspace_misses_rate": master_runtime.get("keyspace_misses_rate"),
                "mem_fragmentation_ratio": master_runtime.get("mem_fragmentation_ratio"),
            },
            "nodes": nodes_payload,
            "databases": databases_payload,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
