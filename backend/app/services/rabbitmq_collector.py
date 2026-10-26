"""
Service for collecting RabbitMQ data through the management HTTP API.
"""
import base64
import json
import logging
import ssl
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode
from urllib.request import Request
from app.services.http_client import open_http as urlopen
from urllib.error import HTTPError, URLError

from app.config import get_app_config
from app.monitor_logging import log_monitor_request
from app.services.infrastructure_metrics import enrich_node_resources

logger = logging.getLogger(__name__)


class RabbitMQCollector:
    """Collect lightweight RabbitMQ data with low load profile."""

    def _is_maintenance_mode(self, node_payload: Dict[str, Any]) -> bool:
        """Detect maintenance mode from RabbitMQ node payload (version-dependent fields)."""
        direct_flags = [
            node_payload.get("maintenance"),
            node_payload.get("maintenance_mode"),
            node_payload.get("in_maintenance"),
        ]
        if any(bool(flag) for flag in direct_flags):
            return True

        maintenance_status = node_payload.get("maintenance_status")
        if isinstance(maintenance_status, str):
            return maintenance_status.strip().lower() in {"enabled", "on", "active", "true"}
        if isinstance(maintenance_status, dict):
            status_value = str(maintenance_status.get("status", "")).strip().lower()
            return status_value in {"enabled", "on", "active", "true"}

        maintenance_mode_status = node_payload.get("maintenance_mode_status")
        if isinstance(maintenance_mode_status, str):
            return maintenance_mode_status.strip().lower() in {"enabled", "on", "active", "true"}
        if isinstance(maintenance_mode_status, dict):
            status_value = str(maintenance_mode_status.get("status", "")).strip().lower()
            return status_value in {"enabled", "on", "active", "true"}
        return False

    def _calculate_node_status(self, node_payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """Calculate normalized node status label for UI."""
        running = bool(node_payload.get("running", False))
        if not running:
            return {"status": "UNREACHABLE", "status_reason": "Node is not running or unreachable."}

        if self._is_maintenance_mode(node_payload):
            return {"status": "MAINTENANCE", "status_reason": "Node is in maintenance mode."}

        mem_alarm = bool(node_payload.get("mem_alarm", False))
        disk_alarm = bool(node_payload.get("disk_free_alarm", False))
        if mem_alarm or disk_alarm:
            reasons: List[str] = []
            if mem_alarm:
                reasons.append("memory alarm")
            if disk_alarm:
                reasons.append("disk alarm")
            return {
                "status": "ALARM",
                "status_reason": f"Active {' and '.join(reasons)}.",
            }

        fd_used = int(node_payload.get("fd_used", 0) or 0)
        fd_total = int(node_payload.get("fd_total", 0) or 0)
        sockets_used = int(node_payload.get("sockets_used", 0) or 0)
        sockets_total = int(node_payload.get("sockets_total", 0) or 0)
        near_limit = (
            (fd_total > 0 and (fd_used / fd_total) >= 0.9)
            or (sockets_total > 0 and (sockets_used / sockets_total) >= 0.9)
        )
        if near_limit:
            return {
                "status": "LIMITED",
                "status_reason": "File descriptors or sockets usage is close to the configured limit.",
            }

        return {"status": "RUNNING", "status_reason": None}

    def _extract_rabbitmq_version(self, node_payload: Dict[str, Any]) -> Optional[str]:
        """Extract RabbitMQ application version from node payload."""
        for application in node_payload.get("running_applications", []) or []:
            if isinstance(application, dict):
                if application.get("name") == "rabbit":
                    version = application.get("version")
                    return str(version) if version else None
            elif isinstance(application, (list, tuple)) and len(application) >= 2:
                # RabbitMQ API commonly returns [name, version, description].
                if str(application[0]) == "rabbit":
                    version = application[1]
                    return str(version) if version else None
        # Fallback for alternative payload shape.
        direct_version = node_payload.get("rabbitmq_version")
        if direct_version:
            return str(direct_version)
        product_version = node_payload.get("product_version")
        return str(product_version) if product_version else None

    def _extract_erlang_version(self, node_payload: Dict[str, Any]) -> Optional[str]:
        """Extract Erlang version from node payload."""
        erlang_version = node_payload.get("erlang_version")
        if erlang_version:
            return str(erlang_version)
        otp_release = node_payload.get("otp_release")
        if otp_release:
            return str(otp_release)
        return None

    def _get_target_config(self, target_name: str):
        current_config = get_app_config()
        if not current_config:
            return None
        for target in current_config.rabbitmq_targets:
            if target.name == target_name:
                return target
        return None

    def _build_base_urls(self, host: str, options: Dict[str, Any]) -> List[str]:
        """Build candidate base URLs for RabbitMQ management API."""
        host_value = (host or "").strip()
        if not host_value:
            return []

        if host_value.startswith("http://") or host_value.startswith("https://"):
            return [host_value.rstrip("/")]

        preferred_scheme = str(options.get("scheme", "") or "").strip().lower()
        http_port = int(options.get("managementPortHttp", 15672) or 15672)
        https_port = int(options.get("managementPortHttps", 15671) or 15671)
        plain_http_port = int(options.get("plainHttpPort", 80) or 80)
        plain_https_port = int(options.get("plainHttpsPort", 443) or 443)

        # If port is explicitly provided in host, try both schemes with that port.
        if ":" in host_value:
            port_candidates = [f"http://{host_value}", f"https://{host_value}"]
            if preferred_scheme == "https":
                return [port_candidates[1], port_candidates[0]]
            if preferred_scheme == "http":
                return port_candidates
            return port_candidates

        candidates = [
            f"http://{host_value}:{http_port}",
            f"https://{host_value}:{https_port}",
            f"https://{host_value}:{plain_https_port}",
            f"http://{host_value}:{plain_http_port}",
        ]

        if preferred_scheme == "https":
            candidates = [c for c in candidates if c.startswith("https://")] + [
                c for c in candidates if c.startswith("http://")
            ]
        elif preferred_scheme == "http":
            candidates = [c for c in candidates if c.startswith("http://")] + [
                c for c in candidates if c.startswith("https://")
            ]

        if preferred_scheme == "https" and not options.get("allowHttpFallback", False):
            candidates = [candidate for candidate in candidates if candidate.startswith("https://")]

        # Remove duplicates while preserving order.
        seen = set()
        deduped: List[str] = []
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                deduped.append(candidate.rstrip("/"))
        return deduped

    def _build_api_path(self, api_path: str, options: Dict[str, Any]) -> str:
        management_path = str(options.get("managementPath", "") or "").strip()
        if not management_path:
            return api_path
        normalized_prefix = f"/{management_path.strip('/')}"
        return f"{normalized_prefix}{api_path}"

    def _request_json(
        self,
        base_url: str,
        path: str,
        username: Optional[str],
        password: Optional[str],
        timeout_s: float = 2.5,
        verify_tls: bool = True,
    ) -> Any:
        request = Request(f"{base_url}{path}")
        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            request.add_header("Authorization", f"Basic {token}")
        request.add_header("Accept", "application/json")
        ssl_context = None
        if base_url.startswith("https://") and not verify_tls:
            # For internal environments with self-signed certs.
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        with urlopen(request, timeout=timeout_s, context=ssl_context) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}

    def _request_empty(
        self,
        base_url: str,
        path: str,
        username: Optional[str],
        password: Optional[str],
        method: str,
        timeout_s: float = 4.0,
        verify_tls: bool = True,
    ) -> int:
        request = Request(f"{base_url}{path}", method=method.upper())
        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            request.add_header("Authorization", f"Basic {token}")
        request.add_header("Accept", "*/*")
        ssl_context = None
        if base_url.startswith("https://") and not verify_tls:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        with urlopen(request, timeout=timeout_s, context=ssl_context) as response:
            response.read()
            return int(response.status)

    def _http_error_detail(self, exc: HTTPError) -> str:
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        if not body:
            return f"HTTP {exc.code}"
        try:
            payload = json.loads(body)
            reason = payload.get("reason") or payload.get("error") or payload.get("message")
            if reason:
                return f"HTTP {exc.code}: {reason}"
        except Exception:
            pass
        return f"HTTP {exc.code}: {body[:300]}"

    def _queue_api_path(self, options: Dict[str, Any], vhost: str, queue_name: str, suffix: str = "") -> str:
        encoded_vhost = quote(vhost or "/", safe="")
        encoded_queue = quote(queue_name, safe="")
        return self._build_api_path(f"/api/queues/{encoded_vhost}/{encoded_queue}{suffix}", options)

    def _extract_overview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        queue_totals = payload.get("queue_totals") or {}
        object_totals = payload.get("object_totals") or {}
        message_stats = payload.get("message_stats") if isinstance(payload.get("message_stats"), dict) else {}
        return {
            "rabbitmq_version": (
                str(payload.get("rabbitmq_version"))
                if payload.get("rabbitmq_version") is not None
                else None
            ),
            "erlang_version": (
                str(payload.get("erlang_version"))
                if payload.get("erlang_version") is not None
                else None
            ),
            "messages": int(queue_totals.get("messages", 0) or 0),
            "messages_ready": int(queue_totals.get("messages_ready", 0) or 0),
            "messages_unacknowledged": int(queue_totals.get("messages_unacknowledged", 0) or 0),
            "queues": int(object_totals.get("queues", 0) or 0),
            "exchanges": int(object_totals.get("exchanges", 0) or 0),
            "connections": int(object_totals.get("connections", 0) or 0),
            "channels": int(object_totals.get("channels", 0) or 0),
            "consumers": int(object_totals.get("consumers", 0) or 0),
            "publish_rate": self._message_rate(message_stats, "publish"),
            "deliver_get_rate": self._message_rate(message_stats, "deliver_get"),
            "ack_rate": self._message_rate(message_stats, "ack"),
            "redeliver_rate": self._message_rate(message_stats, "redeliver"),
        }

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    @classmethod
    def _message_rate(cls, stats: Dict[str, Any], key: str) -> Optional[float]:
        details = stats.get(f"{key}_details") if isinstance(stats, dict) else None
        if isinstance(details, dict):
            rate = cls._to_float(details.get("rate"))
            if rate is not None:
                return round(rate, 2)
        return None

    @staticmethod
    def _queue_type(queue: Dict[str, Any]) -> Optional[str]:
        queue_type = queue.get("type")
        if queue_type:
            return str(queue_type)
        arguments = queue.get("arguments") if isinstance(queue.get("arguments"), dict) else {}
        x_queue_type = arguments.get("x-queue-type") if isinstance(arguments, dict) else None
        return str(x_queue_type) if x_queue_type else None

    @staticmethod
    def _extract_listeners(node_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        listeners = node_payload.get("listeners") if isinstance(node_payload.get("listeners"), list) else []
        result: List[Dict[str, Any]] = []
        for listener in listeners:
            if not isinstance(listener, dict):
                continue
            raw_port = listener.get("port")
            try:
                port = int(raw_port)
            except Exception:
                continue
            result.append(
                {
                    "protocol": str(listener.get("protocol")) if listener.get("protocol") else None,
                    "ip_address": str(listener.get("ip_address")) if listener.get("ip_address") else None,
                    "port": port,
                }
            )
        result.sort(key=lambda item: (str(item.get("protocol") or ""), int(item.get("port") or 0)))
        return result

    def _extract_cluster_listeners(self, nodes_payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        listeners_by_key: Dict[tuple, Dict[str, Any]] = {}
        for node in nodes_payload or []:
            for listener in self._extract_listeners(node):
                key = (
                    listener.get("protocol") or "",
                    listener.get("ip_address") or "",
                    int(listener.get("port") or 0),
                )
                listeners_by_key[key] = listener
        return sorted(
            listeners_by_key.values(),
            key=lambda item: (str(item.get("protocol") or ""), int(item.get("port") or 0), str(item.get("ip_address") or "")),
        )

    def manage_queue(self, target_name: str, vhost: str, queue_name: str, action: str) -> Dict[str, Any]:
        """Run a destructive RabbitMQ queue action through the management API."""
        target_config = self._get_target_config(target_name)
        if not target_config:
            raise ValueError(f"RabbitMQ target {target_name} not found in config")

        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"purge", "delete"}:
            raise ValueError(f"Unsupported RabbitMQ queue action: {action}")

        queue_name = str(queue_name or "")
        vhost = str(vhost or "/")
        if not queue_name:
            raise ValueError("RabbitMQ queue name is required")

        nodes = target_config.nodes or []
        options = target_config.options or {}
        verify_tls = bool(options.get("verifyTls", True))
        timeout_s = float(options.get("queueActionTimeoutS", 4.0) or 4.0)
        suffix = "/contents" if normalized_action == "purge" else ""
        path = self._queue_api_path(options, vhost, queue_name, suffix=suffix)
        last_error: Optional[str] = None

        for node in nodes:
            host_value = node.get("host") if isinstance(node, dict) else str(node)
            if not host_value:
                continue
            for base_url in self._build_base_urls(host_value, options):
                try:
                    status_code = self._request_empty(
                        base_url,
                        path,
                        target_config.username,
                        target_config.password,
                        method="DELETE",
                        timeout_s=timeout_s,
                        verify_tls=verify_tls,
                    )
                    return {
                        "status": "ok",
                        "action": normalized_action,
                        "target_name": target_name,
                        "vhost": vhost,
                        "queue": queue_name,
                        "http_status": status_code,
                    }
                except HTTPError as exc:
                    last_error = self._http_error_detail(exc)
                    if exc.code in {401, 403}:
                        raise PermissionError(
                            f"RabbitMQ credentials are not allowed to {normalized_action} queue {vhost}/{queue_name}"
                        ) from exc
                    if exc.code == 404:
                        raise FileNotFoundError(f"RabbitMQ queue {vhost}/{queue_name} not found") from exc
                    logger.warning(
                        "RabbitMQ target %s: queue %s failed via %s: %s",
                        target_name,
                        normalized_action,
                        base_url,
                        last_error,
                    )
                    raise RuntimeError(
                        f"RabbitMQ queue action {normalized_action} failed for {target_name}: {last_error}"
                    ) from exc
                except URLError as exc:
                    last_error = str(exc.reason)
                    logger.warning(
                        "RabbitMQ target %s: queue %s URL error via %s: %s",
                        target_name,
                        normalized_action,
                        base_url,
                        exc.reason,
                    )
                    continue
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning(
                        "RabbitMQ target %s: queue %s failed via %s: %s",
                        target_name,
                        normalized_action,
                        base_url,
                        exc,
                    )
                    continue

        raise RuntimeError(
            f"RabbitMQ queue action {normalized_action} failed for {target_name}: {last_error or 'all nodes are unreachable'}"
        )

    def _extract_nodes(
        self,
        payload: List[Dict[str, Any]],
        default_rabbitmq_version: Optional[str] = None,
        default_erlang_version: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        nodes: List[Dict[str, Any]] = []
        for node in payload or []:
            rabbitmq_version = self._extract_rabbitmq_version(node) or default_rabbitmq_version
            erlang_version = self._extract_erlang_version(node) or default_erlang_version
            status_payload = self._calculate_node_status(node)
            raw_uptime = node.get("uptime")
            uptime_seconds: Optional[int] = None
            if isinstance(raw_uptime, (int, float)):
                # RabbitMQ management API typically returns uptime in milliseconds.
                uptime_seconds = max(0, int(raw_uptime / 1000))
            cluster_links = node.get("cluster_links") if isinstance(node.get("cluster_links"), list) else []
            cluster_link_peers: List[str] = []
            for link in cluster_links:
                if isinstance(link, dict) and link.get("name"):
                    cluster_link_peers.append(str(link.get("name")))
            partitions = node.get("partitions") if isinstance(node.get("partitions"), list) else []
            enabled_plugins = node.get("enabled_plugins") if isinstance(node.get("enabled_plugins"), list) else []
            node_type = node.get("type")
            role = str(node_type).upper() if node_type in ("disc", "ram") else None
            nodes.append(
                {
                    "name": node.get("name", "unknown"),
                    "running": bool(node.get("running", False)),
                    "role": role,
                    "status": status_payload["status"] or "UNREACHABLE",
                    "status_reason": status_payload["status_reason"],
                    "rabbitmq_version": rabbitmq_version,
                    "erlang_version": erlang_version,
                    "uptime_seconds": uptime_seconds,
                    "channels": int(node.get("channels", 0) or 0),
                    "connections": int(node.get("connections", 0) or 0),
                    "mem_alarm": bool(node.get("mem_alarm", False)),
                    "disk_free_alarm": bool(node.get("disk_free_alarm", False)),
                    "being_drained": bool(node.get("being_drained", False)),
                    "partitions": [str(partition) for partition in partitions],
                    "cluster_links_count": len(cluster_links),
                    "cluster_link_peers": cluster_link_peers[:5],
                    "enabled_plugins": [str(plugin) for plugin in enabled_plugins],
                    "mem_used": node.get("mem_used"),
                    "mem_limit": node.get("mem_limit"),
                    "disk_free": node.get("disk_free"),
                    "disk_free_limit": node.get("disk_free_limit"),
                    "sockets_used": node.get("sockets_used"),
                    "sockets_total": node.get("sockets_total"),
                    "fd_used": node.get("fd_used"),
                    "fd_total": node.get("fd_total"),
                }
            )
        return nodes

    def _calculate_channels_running(self, nodes_payload: List[Dict[str, Any]]) -> Optional[int]:
        """Sum channels only for running nodes."""
        if not isinstance(nodes_payload, list) or len(nodes_payload) == 0:
            return None
        running_nodes = [node for node in nodes_payload if bool(node.get("running", False))]
        if not running_nodes:
            return 0
        total = 0
        has_channels_value = False
        for node in running_nodes:
            raw_channels = node.get("channels")
            if isinstance(raw_channels, (int, float)):
                has_channels_value = True
                total += int(raw_channels)
        if not has_channels_value:
            return None
        return total

    def _extract_queues(self, payload: List[Dict[str, Any]], max_queues: int = 100) -> List[Dict[str, Any]]:
        queues: List[Dict[str, Any]] = []
        for queue in payload or []:
            message_stats = queue.get("message_stats") if isinstance(queue.get("message_stats"), dict) else {}
            queues.append(
                {
                    "name": queue.get("name", "unknown"),
                    "vhost": queue.get("vhost", "/"),
                    "state": queue.get("state"),
                    "type": self._queue_type(queue),
                    "durable": bool(queue.get("durable", False)),
                    "auto_delete": bool(queue.get("auto_delete", False)),
                    "exclusive": bool(queue.get("exclusive", False)),
                    "consumers": int(queue.get("consumers", 0) or 0),
                    "messages": int(queue.get("messages", 0) or 0),
                    "messages_ready": int(queue.get("messages_ready", 0) or 0),
                    "messages_unacknowledged": int(queue.get("messages_unacknowledged", 0) or 0),
                    "memory_bytes": int(queue.get("memory", 0) or 0) if queue.get("memory") is not None else None,
                    "consumer_utilisation": self._to_float(queue.get("consumer_utilisation")),
                    "publish_rate": self._message_rate(message_stats, "publish"),
                    "deliver_get_rate": self._message_rate(message_stats, "deliver_get"),
                    "ack_rate": self._message_rate(message_stats, "ack"),
                    "redeliver_rate": self._message_rate(message_stats, "redeliver"),
                }
            )

        queues.sort(key=lambda item: (-item["messages"], item["name"]))
        return queues[:max_queues]

    def _extract_users(self, payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract users from RabbitMQ /api/users response."""
        users: List[Dict[str, Any]] = []
        for user in payload or []:
            name = user.get("name")
            if not name:
                continue
            tags = user.get("tags")
            # Tags can be a list or comma-separated string
            if isinstance(tags, list):
                tags_str = ",".join(str(tag) for tag in tags if tag)
            elif isinstance(tags, str):
                tags_str = tags
            else:
                tags_str = None
            users.append(
                {
                    "name": str(name),
                    "tags": tags_str,
                }
            )
        users.sort(key=lambda item: item["name"].lower())
        return users

    def _refetch_queue_consumers(
        self,
        base_url: str,
        options: Dict[str, Any],
        username: Optional[str],
        password: Optional[str],
        timeout_s: float,
        verify_tls: bool,
    ) -> Dict[str, int]:
        """Fetch consumers only, used as fallback when queue payload is too stripped."""
        consumer_params = urlencode({"columns": "name,vhost,consumers"})
        payload = self._request_json(
            base_url,
            self._build_api_path(f"/api/queues?{consumer_params}", options),
            username,
            password,
            timeout_s=timeout_s,
            verify_tls=verify_tls,
        )
        consumers_map: Dict[str, int] = {}
        if isinstance(payload, list):
            for queue in payload:
                queue_name = str(queue.get("name", ""))
                vhost = str(queue.get("vhost", "/"))
                if not queue_name:
                    continue
                key = f"{vhost}:{queue_name}"
                consumers_map[key] = int(queue.get("consumers", 0) or 0)
        return consumers_map

    def _collect_consumers_activity(
        self,
        base_url: str,
        options: Dict[str, Any],
        username: Optional[str],
        password: Optional[str],
        timeout_s: float,
        verify_tls: bool,
    ) -> Dict[str, Any]:
        """Collect consumers activity and per-queue counts from /api/consumers."""
        consumers_params = urlencode({"columns": "active,activity_status,queue"})
        payload = self._request_json(
            base_url,
            self._build_api_path(f"/api/consumers?{consumers_params}", options),
            username,
            password,
            timeout_s=timeout_s,
            verify_tls=verify_tls,
        )
        total_consumers = 0
        active_consumers = 0
        per_queue_counts: Dict[str, int] = {}
        if isinstance(payload, list):
            total_consumers = len(payload)
            for consumer in payload:
                if not isinstance(consumer, dict):
                    continue
                queue_info = consumer.get("queue") if isinstance(consumer.get("queue"), dict) else {}
                queue_name = str(queue_info.get("name", ""))
                vhost = str(queue_info.get("vhost", "/"))
                if queue_name:
                    key = f"{vhost}:{queue_name}"
                    per_queue_counts[key] = per_queue_counts.get(key, 0) + 1
                is_active = bool(consumer.get("active", False))
                if not is_active:
                    activity_status = str(consumer.get("activity_status", "")).strip().lower()
                    is_active = activity_status in {"up", "active", "running"}
                if is_active:
                    active_consumers += 1
        return {
            "total_consumers": total_consumers,
            "active_consumers": active_consumers,
            "per_queue_counts": per_queue_counts,
        }

    def collect_target_data(self, target_name: str) -> Optional[Dict[str, Any]]:
        target_config = self._get_target_config(target_name)
        if not target_config:
            logger.warning("RabbitMQ target %s not found in config", target_name)
            return None

        nodes = target_config.nodes or []
        options = target_config.options or {}
        verify_tls = bool(options.get("verifyTls", True))
        default_timeout = float(options.get("timeoutS", 4.0) or 4.0)
        queues_timeout = float(options.get("queuesTimeoutS", max(default_timeout, 6.0)) or max(default_timeout, 6.0))

        for node in nodes:
            host_value = node.get("host") if isinstance(node, dict) else str(node)
            if not host_value:
                continue
            for base_url in self._build_base_urls(host_value, options):
                try:
                    overview_payload = self._request_json(
                        base_url,
                        self._build_api_path("/api/overview", options),
                        target_config.username,
                        target_config.password,
                        timeout_s=default_timeout,
                        verify_tls=verify_tls,
                    )
                    nodes_payload = self._request_json(
                        base_url,
                        self._build_api_path("/api/nodes", options),
                        target_config.username,
                        target_config.password,
                        timeout_s=default_timeout,
                        verify_tls=verify_tls,
                    )
                    queue_params = urlencode(
                        {
                            "enable_queue_totals": "true",
                            "columns": ",".join(
                                [
                                    "name",
                                    "vhost",
                                    "state",
                                    "type",
                                    "durable",
                                    "auto_delete",
                                    "exclusive",
                                    "arguments",
                                    "consumers",
                                    "consumer_utilisation",
                                    "messages",
                                    "messages_ready",
                                    "messages_unacknowledged",
                                    "memory",
                                    "message_stats",
                                ]
                            ),
                        }
                    )
                    queues_payload = self._request_json(
                        base_url,
                        self._build_api_path(f"/api/queues?{queue_params}", options),
                        target_config.username,
                        target_config.password,
                        timeout_s=queues_timeout,
                        verify_tls=verify_tls,
                    )
                    overview_data = self._extract_overview(
                        overview_payload if isinstance(overview_payload, dict) else {}
                    )
                    raw_nodes_payload = nodes_payload if isinstance(nodes_payload, list) else []
                    listeners_data = self._extract_cluster_listeners(raw_nodes_payload)
                    if listeners_data:
                        overview_data["listeners"] = listeners_data
                    channels_running = self._calculate_channels_running(
                        raw_nodes_payload
                    )
                    if channels_running is not None:
                        overview_data["channels_running"] = channels_running
                    elif overview_data.get("channels", 0) > 0:
                        # Fallback when nodes endpoint doesn't expose per-node channels.
                        overview_data["channels_running"] = int(overview_data.get("channels", 0))
                    nodes_data = self._extract_nodes(
                        raw_nodes_payload,
                        default_rabbitmq_version=overview_data.get("rabbitmq_version"),
                        default_erlang_version=overview_data.get("erlang_version"),
                    )
                    nodes_data = enrich_node_resources(nodes_data, target_config)
                    queues_data = self._extract_queues(queues_payload if isinstance(queues_payload, list) else [])

                    # Collect users (may require admin permissions)
                    users_data: List[Dict[str, Any]] = []
                    try:
                        users_payload = self._request_json(
                            base_url,
                            self._build_api_path("/api/users", options),
                            target_config.username,
                            target_config.password,
                            timeout_s=default_timeout,
                            verify_tls=verify_tls,
                        )
                        users_data = self._extract_users(users_payload if isinstance(users_payload, list) else [])
                    except HTTPError as users_exc:
                        # Users endpoint may require admin permissions, log but don't fail
                        if users_exc.code == 403:
                            log_monitor_request(
                                logger,
                                "rabbitmq",
                                "get_users_forbidden",
                                endpoint=base_url,
                                target_name=target_name,
                            )
                        else:
                            logger.warning(
                                "RabbitMQ target %s: could not fetch users via %s: HTTP %s",
                                target_name,
                                base_url,
                                users_exc.code,
                            )
                    except Exception as users_exc:
                        logger.warning(
                            "RabbitMQ target %s: could not fetch users via %s: %s",
                            target_name,
                            base_url,
                            users_exc,
                        )

                    consumers_activity = self._collect_consumers_activity(
                        base_url=base_url,
                        options=options,
                        username=target_config.username,
                        password=target_config.password,
                        timeout_s=queues_timeout,
                        verify_tls=verify_tls,
                    )
                    overview_data["consumers"] = int(consumers_activity.get("active_consumers", 0))
                    overview_data["consumers_total"] = int(consumers_activity.get("total_consumers", 0))
                    per_queue_counts = consumers_activity.get("per_queue_counts", {})
                    if isinstance(per_queue_counts, dict):
                        for queue in queues_data:
                            key = f"{queue['vhost']}:{queue['name']}"
                            if key in per_queue_counts:
                                queue["consumers"] = int(per_queue_counts[key])

                    # Some Rabbit setups with restricted fields may return consumers=0 for all queues.
                    # Retry consumers-only query and patch values when that happens.
                    if queues_data and all(queue["consumers"] == 0 for queue in queues_data):
                        try:
                            consumers_map = self._refetch_queue_consumers(
                                base_url=base_url,
                                options=options,
                                username=target_config.username,
                                password=target_config.password,
                                timeout_s=queues_timeout,
                                verify_tls=verify_tls,
                            )
                            for queue in queues_data:
                                key = f"{queue['vhost']}:{queue['name']}"
                                if key in consumers_map:
                                    queue["consumers"] = consumers_map[key]
                        except Exception as consumers_exc:
                            logger.warning(
                                "RabbitMQ target %s: could not refetch consumers via %s: %s",
                                target_name,
                                base_url,
                                consumers_exc,
                            )
                    return {
                        "name": target_name,
                        "overview": overview_data,
                        "nodes": nodes_data,
                        "queues": queues_data,
                        "users": users_data if users_data else None,
                        "last_update": datetime.now(timezone.utc).isoformat(),
                    }
                except HTTPError as exc:
                    logger.warning(
                        "RabbitMQ target %s: HTTP %s via %s",
                        target_name,
                        exc.code,
                        base_url,
                    )
                    continue
                except URLError as exc:
                    logger.warning(
                        "RabbitMQ target %s: URL error via %s: %s",
                        target_name,
                        base_url,
                        exc.reason,
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "RabbitMQ target %s: failed via %s: %s",
                        target_name,
                        base_url,
                        exc,
                    )
                    continue

        logger.error("RabbitMQ target %s: all nodes are unreachable", target_name)
        return None
