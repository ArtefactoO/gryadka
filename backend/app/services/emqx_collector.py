"""
EMQX collector.

The collector supports EMQX 4 and 5. By default it probes v5 first, then v4,
because RC has both generations. Prometheus stats can be collected without
auth where the endpoint allows it; authenticated API data is used for richer
per-node details when username/password or a raw Basic auth pair are provided.
"""
import base64
import json
import logging
import re
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request
from app.services.http_client import open_http as urlopen

from app.config import TargetConfig, get_app_config
from app.services.kubernetes_pod_watch import (
    build_incluster_api_context,
    build_label_selector,
    k8s_get_json,
    pod_dict_to_state,
)
from app.services.kubernetes_pod_resources import enrich_pods_with_resources

logger = logging.getLogger(__name__)

METRIC_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+eE0-9.]+)")
LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

COUNTER_METRICS = (
    "emqx_messages_received",
    "emqx_messages_sent",
    "emqx_bytes_received",
    "emqx_bytes_sent",
    "emqx_packets_received",
    "emqx_packets_sent",
)

API_COUNTER_MAP = {
    "messages.received": "emqx_messages_received",
    "messages.sent": "emqx_messages_sent",
    "bytes.received": "emqx_bytes_received",
    "bytes.sent": "emqx_bytes_sent",
    "packets.received": "emqx_packets_received",
    "packets.sent": "emqx_packets_sent",
}

NODE_OK_STATUSES = {"UP", "RUNNING", "OK"}


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _first_number(*values: Any) -> Optional[float]:
    for value in values:
        converted = _to_float(value)
        if converted is not None:
            return converted
    return None


def _format_node_host(node_name: str) -> str:
    if "@" in node_name:
        node_name = node_name.split("@", 1)[1]
    if "." in node_name:
        return node_name.split(".", 1)[0]
    return node_name


def _seconds_since(timestamp: Any) -> Optional[int]:
    value = str(timestamp or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - parsed).total_seconds()
    return max(0, int(seconds))


def _normalize_api_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _lookup_case_insensitive(data: Dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in data.items()}
    for name in names:
        key = name.lower()
        if key in lowered:
            return lowered[key]
    return None


def _listener_status(value: Any) -> str:
    if isinstance(value, dict):
        running = _lookup_case_insensitive(value, "running")
        if running is not None:
            return "UP" if str(running).lower() == "true" or running is True else "DOWN"
    normalized = str(value or "").strip().upper()
    if normalized in {"TRUE", "RUNNING", "ENABLED"}:
        return "UP"
    if normalized in {"FALSE", "STOPPED", "DISABLED"}:
        return "DOWN"
    return normalized or "UNKNOWN"


def _is_node_up(node: Dict[str, Any]) -> bool:
    return str(node.get("status") or "").strip().upper() in NODE_OK_STATUSES


class EMQXCollector:
    """Collect EMQX overview, node and listener data."""

    def __init__(self):
        self._rate_state: Dict[Tuple[str, str], Tuple[float, float]] = {}

    def _get_target_config(self, target_name: str) -> Optional[TargetConfig]:
        current_config = get_app_config()
        if not current_config:
            return None
        for target in current_config.emqx_targets:
            if target.name == target_name:
                return target
        return None

    def _target_option(self, target: TargetConfig, *names: str, default: Any = None) -> Any:
        options = target.options or {}
        for name in names:
            if name in options:
                return options[name]
        return default

    def _scheme(self, target: TargetConfig) -> str:
        return str(self._target_option(target, "scheme", default="http")).strip() or "http"

    def _port(self, target: TargetConfig) -> int:
        raw = self._target_option(target, "managementPortHttp", "managementPort", "port", default=18083)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 18083

    def _node_management_port(self, node: Dict[str, Any], default_port: int) -> int:
        for key in ("managementPortHttp", "managementPort", "apiPort"):
            if node.get(key) is None:
                continue
            try:
                return int(node.get(key))
            except (TypeError, ValueError):
                continue
        return default_port

    def _api_version_option(self, target: TargetConfig) -> Optional[int]:
        raw = self._target_option(target, "apiVersion", "api_version", default="auto")
        if raw is None or str(raw).strip().lower() in {"", "auto"}:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _candidate_versions(self, target: TargetConfig) -> List[int]:
        configured = self._api_version_option(target)
        if configured in {4, 5}:
            return [configured]
        return [5, 4]

    def _timeout(self, target: TargetConfig) -> float:
        raw = self._target_option(target, "timeoutS", "timeout", default=5)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 5.0

    def _verify_tls(self, target: TargetConfig) -> bool:
        return bool(self._target_option(target, "verifyTls", "verify_tls", default=True))

    def _ssl_context(self, target: TargetConfig):
        if self._scheme(target) != "https" or self._verify_tls(target):
            return None
        return ssl._create_unverified_context()

    def _base_urls(self, target: TargetConfig) -> List[str]:
        urls: List[str] = []
        scheme = self._scheme(target)
        port = self._port(target)
        for node in target.nodes or []:
            host = str(node.get("host") or "").strip()
            if not host:
                continue
            node_port = self._node_management_port(node, port)
            url = f"{scheme}://{host}:{node_port}"
            if url not in urls:
                urls.append(url)
        return urls

    def _headers(self, target: TargetConfig, auth: bool) -> Dict[str, str]:
        headers = {"Accept": "application/json, text/plain;q=0.9"}
        if auth:
            if target.basic_auth:
                raw_auth = str(target.basic_auth).strip()
                if raw_auth.startswith("Basic "):
                    headers["Authorization"] = raw_auth
                else:
                    headers["Authorization"] = f"Basic {base64.b64encode(raw_auth.encode('utf-8')).decode('ascii')}"
            elif target.username and target.password:
                raw = f"{target.username}:{target.password}".encode("utf-8")
                headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"
        return headers

    def _has_auth(self, target: TargetConfig) -> bool:
        return bool(target.basic_auth or (target.username and target.password))

    def _request_text(self, target: TargetConfig, path: str, auth: bool = False) -> str:
        last_error: Optional[Exception] = None
        timeout_s = self._timeout(target)
        context = self._ssl_context(target)
        for base_url in self._base_urls(target):
            url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
            request = Request(url, headers=self._headers(target, auth=auth))
            try:
                with urlopen(request, timeout=timeout_s, context=context) as response:
                    return response.read().decode("utf-8", errors="replace")
            except HTTPError as exc:
                last_error = exc
                if exc.code in (401, 403):
                    raise
                logger.debug("EMQX request failed for %s: HTTP %s", url, exc.code)
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                logger.debug("EMQX request failed for %s: %s", url, exc)
        if last_error:
            raise last_error
        raise RuntimeError("EMQX target has no base URLs")

    def _request_json(self, target: TargetConfig, path: str, auth: bool = True) -> Any:
        return json.loads(self._request_text(target, path, auth=auth))

    def _prometheus_path(self, target: TargetConfig, api_version: int) -> str:
        configured = self._target_option(target, "prometheusPath", "prometheus_path")
        if configured:
            return str(configured)
        return "/api/v5/prometheus/stats" if api_version >= 5 else "/api/v4/emqx_prometheus"

    def _api_path(self, api_version: int, resource: str) -> str:
        return f"/api/v{api_version}/{resource.lstrip('/')}"

    def _request_text_with_optional_auth(self, target: TargetConfig, path: str) -> str:
        try:
            return self._request_text(target, path, auth=False)
        except HTTPError as exc:
            if exc.code not in (401, 403) or not self._has_auth(target):
                raise
            return self._request_text(target, path, auth=True)

    def _parse_prometheus(self, text: str) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
        totals: Dict[str, float] = {}
        per_node: Dict[str, Dict[str, float]] = {}

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = METRIC_RE.match(line)
            if not match:
                continue
            name, raw_labels, raw_value = match.groups()
            value = _to_float(raw_value)
            if value is None:
                continue
            totals[name] = totals.get(name, 0.0) + value

            labels = {
                label_match.group(1): label_match.group(2).encode("utf-8").decode("unicode_escape")
                for label_match in LABEL_RE.finditer(raw_labels or "")
            }
            node_name = labels.get("node") or labels.get("instance")
            if node_name:
                node_metrics = per_node.setdefault(node_name, {})
                node_metrics[name] = node_metrics.get(name, 0.0) + value

        return totals, per_node

    def _rate(self, target_name: str, metric_name: str, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        now = time.time()
        key = (target_name, metric_name)
        previous = self._rate_state.get(key)
        self._rate_state[key] = (now, value)
        if not previous:
            return None
        previous_ts, previous_value = previous
        elapsed = now - previous_ts
        if elapsed <= 0 or value < previous_value:
            return None
        return round((value - previous_value) / elapsed, 2)

    def _build_rates(self, target_name: str, metrics: Dict[str, float]) -> Dict[str, Optional[float]]:
        result: Dict[str, Optional[float]] = {}
        for metric_name in COUNTER_METRICS:
            key = metric_name.replace("emqx_", "")
            result[f"{key}_rate"] = self._rate(target_name, metric_name, metrics.get(metric_name))
        return result

    def _sum_node_rates(self, nodes: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        result: Dict[str, Optional[float]] = {}
        for metric_name in COUNTER_METRICS:
            rate_key = f"{metric_name.replace('emqx_', '')}_rate"
            values = [
                rate
                for rate in (_to_float(node.get(rate_key)) for node in nodes)
                if rate is not None
            ]
            result[rate_key] = round(sum(values), 2) if values else None
        return result

    def _collect_api_nodes(self, target: TargetConfig, api_version: int) -> List[Dict[str, Any]]:
        if not self._has_auth(target):
            return []
        try:
            payload = _normalize_api_payload(self._request_json(target, self._api_path(api_version, "nodes"), auth=True))
        except HTTPError as exc:
            logger.warning("EMQX API nodes request rejected for target %s: HTTP %s", target.name, exc.code)
            return []
        except Exception as exc:
            logger.debug("Could not collect EMQX API nodes for target %s: %s", target.name, exc)
            return []
        if not isinstance(payload, list):
            return []
        nodes: List[Dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            node_name = str(item.get("node") or item.get("name") or item.get("node_name") or "").strip()
            if not node_name:
                continue
            running = item.get("running")
            status = str(
                item.get("status")
                or item.get("node_status")
                or ("UP" if running is not False else "DOWN")
            ).upper()
            if status in {"TRUE", "RUNNING"}:
                status = "UP"
            elif status in {"FALSE", "STOPPED"}:
                status = "DOWN"
            uptime_raw = _first_number(item.get("uptime"), item.get("uptime_seconds"))
            uptime_seconds = None
            if uptime_raw is not None:
                uptime_seconds = int(uptime_raw / 1000) if uptime_raw > 10_000_000 else int(uptime_raw)
            nodes.append(
                {
                    "name": node_name,
                    "host": _format_node_host(node_name),
                    "source": "api",
                    "status": status or "UNKNOWN",
                    "version": item.get("version"),
                    "uptime_seconds": uptime_seconds,
                    "connections": _to_int(item.get("connections") or item.get("live_connections"), 0),
                    "live_connections": _to_int(item.get("live_connections"), 0)
                    if item.get("live_connections") is not None
                    else None,
                    "clients": _to_int(item.get("clients"), 0) if item.get("clients") is not None else None,
                }
            )
        return nodes

    def _collect_api_listeners(self, target: TargetConfig, api_version: int) -> List[Dict[str, Any]]:
        if not self._has_auth(target):
            return []
        try:
            payload = _normalize_api_payload(self._request_json(target, self._api_path(api_version, "listeners"), auth=True))
        except Exception as exc:
            logger.debug("Could not collect EMQX listeners for target %s: %s", target.name, exc)
            return []
        if not isinstance(payload, list):
            return []
        listeners: List[Dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            raw_status = item.get("status")
            bind = str(item.get("bind") or item.get("listen_on") or item.get("bind_address") or "").strip()
            port = None
            if ":" in bind:
                try:
                    port = int(bind.rsplit(":", 1)[1])
                except ValueError:
                    port = None
            elif str(item.get("port") or "").strip():
                port = _to_int(item.get("port"), 0)
            if not bind and port:
                bind = f":{port}"
            current_connections = _lookup_case_insensitive(
                item,
                "current_connections",
                "current_connections.count",
            )
            max_connections = _lookup_case_insensitive(item, "max_connections")
            if isinstance(raw_status, dict):
                current_connections = _lookup_case_insensitive(raw_status, "current_connections") or current_connections
                max_connections = _lookup_case_insensitive(raw_status, "max_connections") or max_connections
            listeners.append(
                {
                    "node": item.get("node"),
                    "protocol": item.get("protocol") or item.get("type") or item.get("name"),
                    "bind": bind or None,
                    "port": port,
                    "status": _listener_status(raw_status),
                    "current_connections": _to_int(current_connections, 0)
                    if current_connections is not None
                    else None,
                    "max_connections": _to_int(max_connections, 0)
                    if max_connections is not None and str(max_connections).lower() != "infinity"
                    else None,
                }
            )
        return listeners

    def _collect_api_stats(self, target: TargetConfig, api_version: int) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
        if not self._has_auth(target):
            return [], {}
        try:
            payload = _normalize_api_payload(self._request_json(target, self._api_path(api_version, "stats"), auth=True))
        except Exception as exc:
            logger.debug("Could not collect EMQX stats for target %s: %s", target.name, exc)
            return [], {}
        if not isinstance(payload, list):
            return [], {}

        nodes: List[Dict[str, Any]] = []
        totals: Dict[str, float] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            node_name = str(item.get("node") or item.get("name") or "").strip()
            if not node_name:
                continue
            node = {
                "name": node_name,
                "host": _format_node_host(node_name),
                "source": "api",
                "connections": _to_int(item.get("connections.count"), 0)
                if item.get("connections.count") is not None
                else None,
                "live_connections": _to_int(item.get("live_connections.count"), 0)
                if item.get("live_connections.count") is not None
                else None,
                "sessions": _to_int(item.get("cluster_sessions.count"), 0)
                if item.get("cluster_sessions.count") is not None
                else None,
                "subscriptions": _to_int(item.get("subscriptions.count"), 0)
                if item.get("subscriptions.count") is not None
                else None,
                "topics": _to_int(item.get("topics.count"), 0)
                if item.get("topics.count") is not None
                else None,
            }
            nodes.append(node)

            if node["connections"] is not None:
                totals["emqx_connections_count"] = totals.get("emqx_connections_count", 0.0) + float(node["connections"])
            if node["live_connections"] is not None:
                totals["emqx_live_connections_count"] = totals.get("emqx_live_connections_count", 0.0) + float(node["live_connections"])
            if node["sessions"] is not None:
                totals["emqx_cluster_sessions_count"] = totals.get("emqx_cluster_sessions_count", 0.0) + float(node["sessions"])
            if node["subscriptions"] is not None:
                totals["emqx_subscriptions_count"] = totals.get("emqx_subscriptions_count", 0.0) + float(node["subscriptions"])
            if node["topics"] is not None:
                totals["emqx_topics_count"] = totals.get("emqx_topics_count", 0.0) + float(node["topics"])
        return nodes, totals

    def _collect_api_metrics(self, target: TargetConfig, api_version: int) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
        if not self._has_auth(target):
            return [], {}
        try:
            payload = _normalize_api_payload(self._request_json(target, self._api_path(api_version, "metrics"), auth=True))
        except Exception as exc:
            logger.debug("Could not collect EMQX metrics for target %s: %s", target.name, exc)
            return [], {}
        if not isinstance(payload, list):
            return [], {}

        nodes: List[Dict[str, Any]] = []
        totals: Dict[str, float] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            node_name = str(item.get("node") or item.get("name") or "").strip()
            if not node_name:
                continue
            node: Dict[str, Any] = {"name": node_name, "host": _format_node_host(node_name), "source": "api"}
            for api_key, prometheus_key in API_COUNTER_MAP.items():
                value = _to_float(item.get(api_key))
                if value is None:
                    continue
                totals[prometheus_key] = totals.get(prometheus_key, 0.0) + value
                short_key = prometheus_key.replace("emqx_", "")
                node[f"{short_key}_rate"] = self._rate(target.name, f"node:{node_name}:{api_key}", value)
            nodes.append(node)
        return nodes, totals

    def _merge_prometheus_nodes(
        self,
        target: TargetConfig,
        api_nodes: List[Dict[str, Any]],
        per_node_metrics: Dict[str, Dict[str, float]],
        extra_nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        by_name = {str(node.get("name")): dict(node) for node in api_nodes if node.get("name")}
        by_host = {
            str(node.get("host")): node
            for node in by_name.values()
            if node.get("host")
        }

        for source_node in extra_nodes or []:
            node_name = str(source_node.get("name") or "").strip()
            host = str(source_node.get("host") or _format_node_host(node_name)).strip()
            if not node_name and not host:
                continue
            node = by_name.get(node_name) if node_name else None
            if not node and host:
                node = by_host.get(host)
            if not node:
                node = {
                    "name": node_name or host,
                    "host": host or _format_node_host(node_name),
                    "source": "api",
                    "status": "UP",
                }
                by_name[str(node["name"])] = node
                if node.get("host"):
                    by_host[str(node["host"])] = node
            for key, value in source_node.items():
                if key in {"name", "host"} or value is None:
                    continue
                node[key] = value

        for node_name, metrics in per_node_metrics.items():
            node = by_name.setdefault(
                node_name,
                {
                    "name": node_name,
                    "host": _format_node_host(node_name),
                    "source": "prometheus",
                    "status": "UP",
                },
            )
            node["connections"] = _to_int(metrics.get("emqx_connections_count"), node.get("connections") or 0)
            node["clients"] = _to_int(metrics.get("emqx_clients_count"), node.get("clients") or 0)
            node["subscriptions"] = _to_int(metrics.get("emqx_subscriptions_count"), node.get("subscriptions") or 0)
            node["topics"] = _to_int(metrics.get("emqx_topics_count"), node.get("topics") or 0)

        if by_name:
            return sorted(by_name.values(), key=lambda item: str(item.get("name") or ""))

        nodes: List[Dict[str, Any]] = []
        for host in self._display_config_node_hosts(target):
            nodes.append({"name": host, "host": host, "source": "config", "status": "UP"})
        return nodes

    def _is_management_endpoint_host(self, target: TargetConfig, host: str) -> bool:
        value = str(host or "").strip().lower()
        if not value:
            return False

        k8s_cfg = (target.options or {}).get("kubernetes") or {}
        service_name = ""
        if isinstance(k8s_cfg, dict):
            service_name = str(k8s_cfg.get("serviceName") or "").strip().lower()

        if service_name and (value == service_name or value.startswith(f"{service_name}.")):
            return True
        return ".svc." in value and ("dashboard" in value or bool(service_name and service_name in value))

    def _display_config_node_hosts(self, target: TargetConfig) -> List[str]:
        hosts: List[str] = []
        for item in target.nodes or []:
            host = str(item.get("host") or "").strip()
            if not host or self._is_management_endpoint_host(target, host):
                continue
            if host not in hosts:
                hosts.append(host)
        return hosts

    def _add_missing_config_nodes(self, target: TargetConfig, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not target.nodes:
            return nodes

        result = list(nodes)
        known_hosts = {
            str(node.get("host") or "").strip()
            for node in result
            if str(node.get("host") or "").strip()
        }
        known_names = {
            str(node.get("name") or "").strip()
            for node in result
            if str(node.get("name") or "").strip()
        }
        for host in self._display_config_node_hosts(target):
            if not host or host in known_hosts or host in known_names:
                continue
            result.append({"name": host, "host": host, "source": "config", "status": "NO_DATA"})
            known_hosts.add(host)
            known_names.add(host)
        return sorted(result, key=lambda item: str(item.get("name") or ""))

    def _collect_k8s_pods(self, target: TargetConfig) -> List[Dict[str, Any]]:
        k8s_cfg = (target.options or {}).get("kubernetes") or {}
        if not isinstance(k8s_cfg, dict) or not k8s_cfg.get("enabled"):
            return []

        namespace = str(k8s_cfg.get("namespace") or "").strip()
        service_name = str(k8s_cfg.get("serviceName") or "").strip()
        label_selector = str(k8s_cfg.get("labelSelector") or "").strip()
        if not label_selector and isinstance(k8s_cfg.get("matchLabels"), dict):
            label_selector = build_label_selector(k8s_cfg.get("matchLabels"))
        if not namespace or (not service_name and not label_selector):
            return []

        ctx = build_incluster_api_context()
        if not ctx:
            return []

        try:
            if not label_selector and service_name:
                service_path = f"/api/v1/namespaces/{quote(namespace, safe='')}/services/{quote(service_name, safe='')}"
                service_payload = k8s_get_json(ctx, service_path, timeout_s=self._timeout(target))
                label_selector = build_label_selector((service_payload.get("spec") or {}).get("selector"))
            if not label_selector:
                return []

            pods_path = (
                f"/api/v1/namespaces/{quote(namespace, safe='')}/pods"
                f"?labelSelector={quote(label_selector, safe='')}"
            )
            pods_payload = k8s_get_json(ctx, pods_path, timeout_s=self._timeout(target))
            pod_states = [
                pod_dict_to_state(pod)
                for pod in pods_payload.get("items", [])
                if isinstance(pod, dict)
            ]
            pod_states = enrich_pods_with_resources(namespace, pod_states)
            return sorted(pod_states, key=lambda pod: str(pod.get("name") or ""))
        except Exception as exc:
            logger.debug("Could not collect EMQX K8s pods for target %s: %s", target.name, exc)
            return []

    def _attach_k8s_pod_details_to_nodes(
        self,
        nodes: List[Dict[str, Any]],
        k8s_pods: List[Dict[str, Any]],
    ) -> None:
        pods_by_name = {
            str(pod.get("name")): pod
            for pod in k8s_pods
            if pod.get("name")
        }
        if not pods_by_name:
            return
        for node in nodes:
            candidates = [
                str(node.get("host") or "").strip(),
                _format_node_host(str(node.get("name") or "").strip()),
                str(node.get("name") or "").strip(),
            ]
            for candidate in candidates:
                pod = pods_by_name.get(candidate)
                if pod:
                    if pod.get("resources"):
                        node["resources"] = pod.get("resources")
                    node["pod_start_time"] = pod.get("start_time")
                    pod_uptime = _seconds_since(pod.get("start_time"))
                    if pod_uptime is not None:
                        node["uptime_seconds"] = pod_uptime
                    break

    def _filter_nodes_by_k8s_pods(
        self,
        nodes: List[Dict[str, Any]],
        k8s_pods: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        pod_names = {
            str(pod.get("name") or "").strip()
            for pod in k8s_pods
            if str(pod.get("name") or "").strip()
        }
        if not pod_names:
            return nodes

        filtered: List[Dict[str, Any]] = []
        for node in nodes:
            candidates = {
                _format_node_host(str(node.get("name") or "").strip()),
                _format_node_host(str(node.get("host") or "").strip()),
            }
            candidates.discard("")
            if candidates & pod_names:
                filtered.append(node)

        # If an unexpected EMQX/K8s naming format prevents matching, keep API
        # data visible instead of hiding the whole cluster.
        return filtered or nodes

    def _k8s_namespace(self, target: TargetConfig) -> Optional[str]:
        k8s_cfg = (target.options or {}).get("kubernetes") or {}
        if not isinstance(k8s_cfg, dict) or not k8s_cfg.get("enabled"):
            return None
        namespace = str(k8s_cfg.get("namespace") or "").strip()
        return namespace or None

    def collect_target_data(self, target_name: str) -> Optional[Dict[str, Any]]:
        target = self._get_target_config(target_name)
        if not target:
            logger.warning("EMQX target %s not found in config", target_name)
            return None

        totals: Dict[str, float] = {}
        per_node_metrics: Dict[str, Dict[str, float]] = {}
        errors: List[str] = []
        detected_api_version: Optional[int] = None

        for api_version in self._candidate_versions(target):
            try:
                prometheus_text = self._request_text_with_optional_auth(target, self._prometheus_path(target, api_version))
                totals, per_node_metrics = self._parse_prometheus(prometheus_text)
                detected_api_version = api_version
                break
            except HTTPError as exc:
                errors.append(f"v{api_version}_prometheus_http_{exc.code}")
                logger.debug("EMQX v%s prometheus stats failed for target %s: HTTP %s", api_version, target_name, exc.code)
            except Exception as exc:
                errors.append(f"v{api_version}_{type(exc).__name__}")
                logger.debug("EMQX v%s prometheus stats failed for target %s: %s", api_version, target_name, exc)

        if detected_api_version is None:
            configured = self._api_version_option(target)
            detected_api_version = configured if configured in {4, 5} else 5
            if not self._has_auth(target):
                logger.warning("EMQX prometheus stats failed for target %s: %s", target_name, ", ".join(errors))

        api_nodes: List[Dict[str, Any]] = []
        api_stat_nodes: List[Dict[str, Any]] = []
        api_metric_nodes: List[Dict[str, Any]] = []
        api_stat_totals: Dict[str, float] = {}
        api_metric_totals: Dict[str, float] = {}
        listeners: List[Dict[str, Any]] = []

        if self._has_auth(target):
            api_versions = [detected_api_version] + [
                version for version in self._candidate_versions(target) if version != detected_api_version
            ]
            for api_version in api_versions:
                api_nodes = self._collect_api_nodes(target, api_version)
                api_stat_nodes, api_stat_totals = self._collect_api_stats(target, api_version)
                api_metric_nodes, api_metric_totals = self._collect_api_metrics(target, api_version)
                listeners = self._collect_api_listeners(target, api_version)
                if api_nodes or api_stat_nodes or api_metric_nodes or listeners:
                    detected_api_version = api_version
                    break

        for key, value in api_stat_totals.items():
            totals.setdefault(key, value)

        # Prometheus stats can be served by any EMQX pod behind a Kubernetes
        # Service. Counter deltas become unstable when consecutive scrapes hit
        # different pods, so prefer authenticated API metrics aggregated by node
        # for read/write rates and keep Prometheus as a fallback only.
        for key, value in api_metric_totals.items():
            totals[key] = value

        nodes = self._merge_prometheus_nodes(
            target,
            api_nodes,
            per_node_metrics,
            extra_nodes=[*api_stat_nodes, *api_metric_nodes],
        )
        k8s_pods = self._collect_k8s_pods(target)
        nodes = self._filter_nodes_by_k8s_pods(nodes, k8s_pods)
        self._attach_k8s_pod_details_to_nodes(nodes, k8s_pods)
        has_data = bool(totals or api_nodes or api_stat_nodes or api_metric_nodes or per_node_metrics)
        if not has_data:
            for node in nodes:
                node["status"] = "NO_DATA"
        else:
            nodes = self._add_missing_config_nodes(target, nodes)

        node_status_total = len(nodes)
        node_status_up = len([node for node in nodes if _is_node_up(node)])
        cluster_nodes_running = _to_int(totals.get("emqx_cluster_nodes_running"), 0)
        cluster_nodes_stopped = _to_int(totals.get("emqx_cluster_nodes_stopped"), 0)
        cluster_nodes_total = cluster_nodes_running + cluster_nodes_stopped
        config_nodes_total = len(self._display_config_node_hosts(target))

        pods_total = 0
        pods_ready = 0
        if k8s_pods:
            pods_total = len(k8s_pods)
            pods_ready = len(
                [
                    pod for pod in k8s_pods
                    if bool(pod.get("ready")) and not bool(pod.get("terminating"))
                ]
            )

        if k8s_pods:
            # EMQX can keep stopped nodes from previous StatefulSet revisions in
            # its API. For Kubernetes-backed targets the actual pod set is the
            # source of truth for current cluster membership.
            nodes_total = max(node_status_total, pods_total, config_nodes_total)
            nodes_running = node_status_up if node_status_total > 0 else pods_ready
        else:
            nodes_total = max(node_status_total, cluster_nodes_total, config_nodes_total)
            nodes_running = node_status_up
            if cluster_nodes_total > 0:
                if node_status_total > 0:
                    nodes_running = min(cluster_nodes_running, node_status_up)
                else:
                    nodes_running = min(cluster_nodes_running, nodes_total)

        if not has_data:
            status = "NO_DATA"
            nodes_running = 0
        elif (nodes_total > 0 and nodes_running <= 0) or (pods_total > 0 and pods_ready <= 0):
            status = "DOWN"
        elif (nodes_total > 0 and nodes_running < nodes_total) or (pods_total > 0 and pods_ready < pods_total):
            status = "DEGRADED"
        elif nodes_total > 0 or pods_total > 0:
            status = "UP"
        elif totals or nodes:
            status = "DOWN"
        else:
            status = "NO_DATA"

        versions = sorted({str(node.get("version")) for node in nodes if node.get("version")})
        rates = self._sum_node_rates(nodes)
        fallback_rates = self._build_rates(target_name, totals)
        for key, value in rates.items():
            if value is None:
                rates[key] = fallback_rates.get(key)
        overview = {
            "status": status,
            "nodes_total": nodes_total,
            "nodes_up": nodes_running,
            "nodes_down": max(0, nodes_total - nodes_running),
            "connections": _to_int(totals.get("emqx_connections_count"), 0),
            "live_connections": _to_int(totals.get("emqx_live_connections_count"), 0),
            "clients": _to_int(totals.get("emqx_clients_count"), 0) if "emqx_clients_count" in totals else None,
            "sessions": _to_int(totals.get("emqx_cluster_sessions_count"), 0)
            if "emqx_cluster_sessions_count" in totals
            else None,
            "subscriptions": _to_int(totals.get("emqx_subscriptions_count"), 0),
            "topics": _to_int(totals.get("emqx_topics_count"), 0),
            "version": versions[0] if versions else None,
            "version_mismatch": len(versions) > 1,
            "api_version": detected_api_version,
            **rates,
        }
        if k8s_pods:
            overview["pods_total"] = pods_total
            overview["pods_ready"] = pods_ready
            overview["pods_not_ready"] = max(0, pods_total - pods_ready)
        namespace = self._k8s_namespace(target)
        if namespace:
            overview["namespace"] = namespace

        return {
            "name": target_name,
            "overview": overview,
            "nodes": nodes,
            "k8s_pods": k8s_pods,
            "listeners": listeners,
            "last_update": _now_iso(),
            "errors": errors,
        }

    def collect_all_targets(self) -> List[Dict[str, Any]]:
        current_config = get_app_config()
        if not current_config:
            return []
        result: List[Dict[str, Any]] = []
        for target in current_config.emqx_targets:
            data = self.collect_target_data(target.name)
            if data:
                result.append(data)
        return result
