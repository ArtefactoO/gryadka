"""
Reusable Kubernetes pod resource enrichment through VictoriaMetrics.
"""
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import get_app_config
from app.services.victoriametrics_client import VictoriaMetricsClient

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _vector_value(item: Dict[str, Any]) -> Optional[float]:
    value = item.get("value")
    if isinstance(value, list) and len(value) >= 2:
        return _to_float(value[1])
    return None


def _metric_label(item: Dict[str, Any], label: str) -> Optional[str]:
    metric = item.get("metric")
    if not isinstance(metric, dict):
        return None
    value = metric.get(label)
    return str(value).strip() if value is not None else None


def _promql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _pod_regex(pod_names: Iterable[str]) -> str:
    escaped = []
    for name in pod_names:
        value = str(name or "").strip()
        if value:
            escaped.append(re.escape(value))
    return "|".join(escaped)


def _add_metric(
    target: Dict[Tuple[str, str], float],
    item: Dict[str, Any],
    *,
    accumulate: bool = False,
) -> None:
    namespace = _metric_label(item, "namespace")
    pod = _metric_label(item, "pod")
    container = _metric_label(item, "container") or ""
    value = _vector_value(item)
    if not namespace or not pod or not container or container == "POD" or value is None:
        return
    key = (pod, container)
    if accumulate:
        target[key] = target.get(key, 0.0) + value
    else:
        target[key] = value


class KubernetesPodResourcesService:
    """Fetch pod CPU/RAM usage, requests and limits and attach them to pod payloads."""

    def __init__(self, base_url: str, timeout_s: float = 3.0, lookback: str = "5m"):
        self.client = VictoriaMetricsClient(base_url=base_url, timeout_s=timeout_s)
        self.lookback = lookback or "5m"

    @classmethod
    def from_config(cls) -> Optional["KubernetesPodResourcesService"]:
        current_config = get_app_config()
        if not current_config:
            return None

        config = current_config.get_victoria_metrics_config()
        if not config or not bool(config.get("enabled", False)):
            return None

        base_url = str(config.get("baseUrl") or config.get("url") or "").strip()
        if not base_url:
            return None

        try:
            timeout_s = float(config.get("timeoutS", 3.0) or 3.0)
        except (TypeError, ValueError):
            timeout_s = 3.0
        lookback = str(config.get("lookback") or "5m").strip() or "5m"
        return cls(base_url=base_url, timeout_s=timeout_s, lookback=lookback)

    def _query_container_values(self, promql: str, *, accumulate: bool = False) -> Dict[Tuple[str, str], float]:
        values: Dict[Tuple[str, str], float] = {}
        for item in self.client.query(promql):
            _add_metric(values, item, accumulate=accumulate)
        return values

    def _collect_vectors(self, namespace: str, pod_names: List[str]) -> Optional[Dict[str, Dict[Tuple[str, str], float]]]:
        pod_matcher = _pod_regex(pod_names)
        if not pod_matcher:
            return None
        selector = f'namespace="{_promql_string(namespace)}",pod=~"{_promql_string(pod_matcher)}",container!="",container!="POD"'
        try:
            cpu_usage = self._query_container_values(
                f"sum by(namespace,pod,container) "
                f"(rate(container_cpu_usage_seconds_total{{{selector}}}[{self.lookback}]))",
                accumulate=True,
            )
            memory_usage = self._query_container_values(
                f"sum by(namespace,pod,container) (container_memory_working_set_bytes{{{selector}}})",
                accumulate=True,
            )
            cpu_requests = self._query_container_values(
                f"kube_pod_container_resource_requests{{{selector},resource=\"cpu\",unit=\"core\"}}"
            )
            if not cpu_requests:
                cpu_requests = self._query_container_values(
                    f"kube_pod_container_resource_requests_cpu_cores{{{selector}}}"
                )
            cpu_limits = self._query_container_values(
                f"kube_pod_container_resource_limits{{{selector},resource=\"cpu\",unit=\"core\"}}"
            )
            if not cpu_limits:
                cpu_limits = self._query_container_values(
                    f"kube_pod_container_resource_limits_cpu_cores{{{selector}}}"
                )
            memory_requests = self._query_container_values(
                f"kube_pod_container_resource_requests{{{selector},resource=\"memory\",unit=\"byte\"}}"
            )
            if not memory_requests:
                memory_requests = self._query_container_values(
                    f"kube_pod_container_resource_requests_memory_bytes{{{selector}}}"
                )
            memory_limits = self._query_container_values(
                f"kube_pod_container_resource_limits{{{selector},resource=\"memory\",unit=\"byte\"}}"
            )
            if not memory_limits:
                memory_limits = self._query_container_values(
                    f"kube_pod_container_resource_limits_memory_bytes{{{selector}}}"
                )
            return {
                "cpu_usage": cpu_usage,
                "cpu_requests": cpu_requests,
                "cpu_limits": cpu_limits,
                "memory_usage": memory_usage,
                "memory_requests": memory_requests,
                "memory_limits": memory_limits,
            }
        except Exception as exc:
            logger.debug("Could not query Kubernetes pod resources from VictoriaMetrics: %s", exc)
            return None

    def enrich_pods(self, namespace: str, pods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        pod_names = [str(pod.get("name") or "").strip() for pod in pods if pod.get("name")]
        if not namespace or not pod_names:
            return pods

        vectors = self._collect_vectors(namespace, pod_names)
        if not vectors:
            return pods

        for pod in pods:
            pod_name = str(pod.get("name") or "").strip()
            if not pod_name:
                continue
            containers = sorted({
                container
                for metric_values in vectors.values()
                for (metric_pod, container) in metric_values.keys()
                if metric_pod == pod_name
            })
            if not containers:
                continue

            resource_containers: List[Dict[str, Any]] = []
            totals: Dict[str, float] = {
                "cpu_usage_cores": 0.0,
                "cpu_request_cores": 0.0,
                "cpu_limit_cores": 0.0,
                "memory_usage_bytes": 0.0,
                "memory_request_bytes": 0.0,
                "memory_limit_bytes": 0.0,
            }
            seen = {key: False for key in totals}

            for container in containers:
                key = (pod_name, container)
                item = {
                    "name": container,
                    "cpu_usage_cores": vectors["cpu_usage"].get(key),
                    "cpu_request_cores": vectors["cpu_requests"].get(key),
                    "cpu_limit_cores": vectors["cpu_limits"].get(key),
                    "memory_usage_bytes": vectors["memory_usage"].get(key),
                    "memory_request_bytes": vectors["memory_requests"].get(key),
                    "memory_limit_bytes": vectors["memory_limits"].get(key),
                }
                for field, value in item.items():
                    if field == "name" or value is None:
                        continue
                    totals[field] += float(value)
                    seen[field] = True
                resource_containers.append({
                    **item,
                    "memory_usage_bytes": int(item["memory_usage_bytes"]) if item["memory_usage_bytes"] is not None else None,
                    "memory_request_bytes": int(item["memory_request_bytes"]) if item["memory_request_bytes"] is not None else None,
                    "memory_limit_bytes": int(item["memory_limit_bytes"]) if item["memory_limit_bytes"] is not None else None,
                })

            pod["resources"] = {
                "namespace": namespace,
                "pod": pod_name,
                "cpu_usage_cores": round(totals["cpu_usage_cores"], 4) if seen["cpu_usage_cores"] else None,
                "cpu_request_cores": round(totals["cpu_request_cores"], 4) if seen["cpu_request_cores"] else None,
                "cpu_limit_cores": round(totals["cpu_limit_cores"], 4) if seen["cpu_limit_cores"] else None,
                "memory_usage_bytes": int(totals["memory_usage_bytes"]) if seen["memory_usage_bytes"] else None,
                "memory_request_bytes": int(totals["memory_request_bytes"]) if seen["memory_request_bytes"] else None,
                "memory_limit_bytes": int(totals["memory_limit_bytes"]) if seen["memory_limit_bytes"] else None,
                "containers": resource_containers,
            }
        return pods


def enrich_pods_with_resources(namespace: str, pods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    service = KubernetesPodResourcesService.from_config()
    if not service:
        return pods
    return service.enrich_pods(namespace, pods)
