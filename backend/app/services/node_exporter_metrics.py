"""
Reusable node exporter enrichment through VictoriaMetrics.
"""
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from app.config import get_app_config
from app.services.victoriametrics_client import VictoriaMetricsClient

logger = logging.getLogger(__name__)

NodeCandidateGetter = Callable[[Dict[str, Any]], Sequence[str]]

EXCLUDED_FILESYSTEM_TYPES = {
    "autofs",
    "bpf",
    "cgroup",
    "cgroup2",
    "configfs",
    "debugfs",
    "devpts",
    "devtmpfs",
    "fusectl",
    "hugetlbfs",
    "mqueue",
    "nsfs",
    "overlay",
    "proc",
    "pstore",
    "securityfs",
    "squashfs",
    "sysfs",
    "tmpfs",
    "tracefs",
}

EXCLUDED_MOUNTPOINTS = {
    "/dev",
    "/proc",
    "/run",
    "/sys",
    "/var/run",
}

EXCLUDED_MOUNTPOINT_PREFIXES = (
    "/dev/",
    "/proc/",
    "/run/",
    "/sys/",
    "/var/run/",
    "/var/lib/kubelet/pods/",
    "/var/lib/docker/overlay",
    "/var/lib/containerd/io.containerd.snapshotter",
    "/var/lib/containers/storage/overlay",
)


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


def _metric_instance(item: Dict[str, Any]) -> Optional[str]:
    metric = item.get("metric")
    if not isinstance(metric, dict):
        return None
    instance = metric.get("instance")
    return str(instance).strip() if instance else None


def _metric_label(item: Dict[str, Any], label: str) -> Optional[str]:
    metric = item.get("metric")
    if not isinstance(metric, dict):
        return None
    value = metric.get(label)
    return str(value).strip() if value is not None else None


def _host_without_port(host: str) -> str:
    value = str(host or "").strip()
    if not value:
        return ""
    if value.count(":") == 1 and value.rsplit(":", 1)[1].isdigit():
        return value.rsplit(":", 1)[0]
    return value


def _short_hostname(host: str) -> str:
    return _host_without_port(host).split(".", 1)[0]


def _instance_host(instance: str) -> str:
    return _host_without_port(instance)


def build_host_candidates(*values: Any) -> List[str]:
    """Build stable host matching candidates for node exporter instance labels."""
    candidates: List[str] = []

    def add(value: Any) -> None:
        raw = str(value or "").strip()
        if not raw:
            return
        host = _host_without_port(raw)
        variants = [raw, host]
        if host and not host.endswith(":9100"):
            variants.append(f"{host}:9100")
        short = _short_hostname(host)
        if short:
            variants.extend([short, f"{short}:9100"])
        for item in variants:
            if item and item not in candidates:
                candidates.append(item)

    for value in values:
        add(value)

    return candidates


def mongo_node_host_candidates(node: Dict[str, Any]) -> List[str]:
    return build_host_candidates(node.get("name"))


def rabbit_node_host_candidates(node: Dict[str, Any]) -> List[str]:
    name = str(node.get("name") or "").strip()
    host = name.split("@", 1)[1] if "@" in name else name
    return build_host_candidates(host, name)


def redis_node_host_candidates(node: Dict[str, Any]) -> List[str]:
    if str(node.get("kind") or "").upper() != "REDIS":
        return []
    return build_host_candidates(node.get("host"), node.get("name"))


def redpanda_node_host_candidates(node: Dict[str, Any]) -> List[str]:
    return build_host_candidates(node.get("host"), node.get("name"))


def elasticsearch_node_host_candidates(node: Dict[str, Any]) -> List[str]:
    return build_host_candidates(
        node.get("host"),
        node.get("ip"),
        node.get("transport_address"),
        node.get("internal_name"),
        node.get("name"),
    )


def _is_relevant_filesystem(mountpoint: Optional[str], fstype: Optional[str]) -> bool:
    if not mountpoint:
        return False
    mountpoint = str(mountpoint).strip()
    fs_type = str(fstype or "").strip().lower()
    if fs_type in EXCLUDED_FILESYSTEM_TYPES:
        return False
    if mountpoint in EXCLUDED_MOUNTPOINTS:
        return False
    return not mountpoint.startswith(EXCLUDED_MOUNTPOINT_PREFIXES)


class NodeExporterMetricsService:
    """Fetch node exporter host metrics and attach them to monitor node payloads."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 3.0,
        lookback: str = "5m",
    ):
        self.client = VictoriaMetricsClient(base_url=base_url, timeout_s=timeout_s)
        self.lookback = lookback or "5m"

    @classmethod
    def from_config(cls) -> Optional["NodeExporterMetricsService"]:
        current_config = get_app_config()
        if not current_config:
            return None

        config = current_config.get_victoria_metrics_config()
        if not config or not bool(config.get("enabled", True)):
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

    def _query_map_by_instance(self, promql: str) -> Dict[str, float]:
        data: Dict[str, float] = {}
        for item in self.client.query(promql):
            instance = _metric_instance(item)
            value = _vector_value(item)
            if instance and value is not None:
                data[instance] = value
        return data

    def _query_nodename_map(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for item in self.client.query("node_uname_info"):
            metric = item.get("metric")
            if not isinstance(metric, dict):
                continue
            instance = str(metric.get("instance") or "").strip()
            nodename = str(metric.get("nodename") or "").strip()
            if instance and nodename:
                mapping[nodename] = instance
                mapping[_short_hostname(nodename)] = instance
        return mapping

    def _query_filesystems(self) -> Dict[str, List[Dict[str, Any]]]:
        sizes: Dict[Tuple[str, str, str, str], float] = {}
        frees: Dict[Tuple[str, str, str, str], float] = {}

        for item in self.client.query("node_filesystem_size_bytes"):
            instance = _metric_instance(item)
            mountpoint = _metric_label(item, "mountpoint")
            fstype = _metric_label(item, "fstype")
            if not instance or not _is_relevant_filesystem(mountpoint, fstype):
                continue
            device = _metric_label(item, "device") or ""
            value = _vector_value(item)
            if value is not None:
                sizes[(instance, mountpoint or "", device, fstype or "")] = value

        for item in self.client.query("node_filesystem_free_bytes"):
            instance = _metric_instance(item)
            mountpoint = _metric_label(item, "mountpoint")
            fstype = _metric_label(item, "fstype")
            if not instance or not _is_relevant_filesystem(mountpoint, fstype):
                continue
            device = _metric_label(item, "device") or ""
            value = _vector_value(item)
            if value is not None:
                frees[(instance, mountpoint or "", device, fstype or "")] = value

        filesystems_by_instance: Dict[str, List[Dict[str, Any]]] = {}
        for key, total in sizes.items():
            if total <= 0:
                continue
            instance, mountpoint, device, fstype = key
            free = frees.get(key)
            used_percent = None
            if free is not None:
                used_percent = round(max(0.0, min(100.0, 100.0 * (1.0 - free / total))), 2)
            filesystems_by_instance.setdefault(instance, []).append({
                "mountpoint": mountpoint,
                "device": device or None,
                "fstype": fstype or None,
                "total": int(total),
                "free": int(free) if free is not None else None,
                "total_bytes": int(total),
                "free_bytes": int(free) if free is not None else None,
                "used_percent": used_percent,
            })

        for filesystems in filesystems_by_instance.values():
            filesystems.sort(key=lambda fs: (fs.get("mountpoint") != "/", str(fs.get("mountpoint") or "")))
        return filesystems_by_instance

    def _collect_vectors(self, raise_errors: bool = False) -> Optional[Dict[str, Any]]:
        try:
            cpu_query = (
                "100 * (1 - avg by(instance) "
                f"(rate(node_cpu_seconds_total{{mode=\"idle\"}}[{self.lookback}])))"
            )
            memory_query = (
                "100 * (1 - "
                "node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)"
            )
            return {
                "cpu": self._query_map_by_instance(cpu_query),
                "cpu_cores": self._query_map_by_instance(
                    "count by(instance) (node_cpu_seconds_total{mode=\"idle\"})"
                ),
                "memory_used": self._query_map_by_instance(memory_query),
                "memory_total": self._query_map_by_instance("node_memory_MemTotal_bytes"),
                "memory_available": self._query_map_by_instance("node_memory_MemAvailable_bytes"),
                "disks": self._query_filesystems(),
                "up": self._query_map_by_instance("up"),
                "nodename": self._query_nodename_map(),
            }
        except Exception as exc:
            if raise_errors:
                raise
            logger.debug("Could not query VictoriaMetrics node exporter metrics: %s", exc)
            return None

    def _resolve_instance(
        self,
        candidates: Iterable[str],
        all_instances: Sequence[str],
        nodename_map: Dict[str, str],
        strict: bool = False,
    ) -> Tuple[Optional[str], Optional[str]]:
        normalized_candidates: List[str] = []
        for candidate in candidates:
            for item in ([candidate, _host_without_port(candidate), f"{_host_without_port(candidate)}:9100"] if strict else build_host_candidates(candidate)):
                if item not in normalized_candidates:
                    normalized_candidates.append(item)

        instance_set = set(all_instances)
        for candidate in normalized_candidates:
            if candidate in instance_set:
                return candidate, "instance"
            if candidate in nodename_map:
                return nodename_map[candidate], "nodename"

        for instance in all_instances:
            host = _instance_host(instance)
            short = _short_hostname(host)
            for candidate in normalized_candidates:
                candidate_host = _host_without_port(candidate)
                if candidate_host == host or (not strict and candidate_host == short):
                    return instance, "instance_host"
        return None, None

    def enrich_nodes(
        self,
        nodes: List[Dict[str, Any]],
        candidate_getter: NodeCandidateGetter,
        strict: bool = False,
        diagnostics: bool = False,
    ) -> List[Dict[str, Any]]:
        if not nodes:
            return nodes

        vectors = self._collect_vectors(raise_errors=True) if diagnostics else self._collect_vectors()
        if not vectors:
            return nodes

        instances = sorted(
            set(vectors["cpu"])
            | set(vectors["cpu_cores"])
            | set(vectors["memory_used"])
            | set(vectors["memory_total"])
            | set(vectors["memory_available"])
            | set(vectors["disks"])
        )
        if not instances:
            if diagnostics:
                for node in nodes:
                    node['infrastructure'].update(reason_code='series_missing',
                        reason='VictoriaMetrics отвечает, но метрик node exporter в источнике нет.')
            return nodes

        for node in nodes:
            try:
                instance, matched_label = self._resolve_instance(
                    candidate_getter(node),
                    instances,
                    vectors["nodename"],
                    strict=strict,
                )
                if not instance:
                    if diagnostics:
                        candidates = ', '.join(str(host) for host in candidate_getter(node))
                        node['infrastructure'].update(reason_code='host_unmatched',
                            reason=f'VictoriaMetrics отвечает, но метрики хоста {candidates} не найдены. Проверьте сбор node exporter и метки instance/nodename.')
                    continue

                cpu_percent = vectors["cpu"].get(instance)
                cpu_cores = vectors["cpu_cores"].get(instance)
                memory_percent = vectors["memory_used"].get(instance)
                memory_total = vectors["memory_total"].get(instance)
                memory_available = vectors["memory_available"].get(instance)
                disks = vectors["disks"].get(instance, [])
                scrape_up = vectors["up"].get(instance)
                node["node_exporter"] = {
                    "matched": True,
                    "matched_label": matched_label,
                    "matched_value": instance,
                    "cpu_usage_percent": round(max(0.0, min(100.0, cpu_percent)), 2)
                    if cpu_percent is not None
                    else None,
                    "cpu_cores": int(cpu_cores) if cpu_cores is not None else None,
                    "memory_used_percent": round(max(0.0, min(100.0, memory_percent)), 2)
                    if memory_percent is not None
                    else None,
                    "memory_total_bytes": int(memory_total) if memory_total is not None else None,
                    "memory_available_bytes": int(memory_available) if memory_available is not None else None,
                    "disks": disks,
                    "scrape_up": bool(scrape_up) if scrape_up is not None else None,
                }
            except Exception as exc:
                logger.debug("Could not enrich node %s with node exporter metrics: %s", node.get("name"), exc)
        return nodes


def enrich_nodes_with_node_exporter(
    nodes: List[Dict[str, Any]],
    candidate_getter: NodeCandidateGetter,
) -> List[Dict[str, Any]]:
    service = NodeExporterMetricsService.from_config()
    if not service:
        return nodes
    return service.enrich_nodes(nodes, candidate_getter)
