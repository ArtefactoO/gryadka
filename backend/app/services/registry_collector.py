"""
Registry collection pipeline: sync repositories, extract source objects, link results.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote

import yaml

try:
    import hcl2  # type: ignore
except Exception:  # pragma: no cover - optional parser import safety
    hcl2 = None

from app.config import get_app_config, settings
from app.models.cluster import MonitoringTargetSummary
from app.models.registry import (
    RegistryAutoRow,
    RegistryCollectResponse,
    RegistryDashboardResponse,
    RegistryDnsValidation,
    RegistryMismatchSummary,
    RegistryRepoSummary,
    RegistrySourceReference,
    RegistrySummary,
)
from app.services.registry_cache import RegistryCacheService
from app.services.registry_gitlab_sync import RegistryGitlabSync
from app.services.registry_linker import RegistryLinker
from app.services.registry_manual_service import RegistryManualService
from app.services.registry_repository import RegistryDatabaseUnavailable, RegistryRepository
from app.services.registry_scope import classify_registry_address

logger = logging.getLogger(__name__)

REGISTRY_COLLECTOR_LOCK_KEY = "lock:collector:registry"
REGISTRY_COLLECTOR_LOCK_TTL_S = 3600

DEFAULT_SERVICE_KEYS = {
    "loadbalancerip",
    "externalips",
    "ports",
    "targetports",
    "servicesname",
    "servicename",
    "fullnameoverride",
    "nameoverride",
    "namespace",
    "hosts",
    "ingressclass",
    "port",
    "protocol",
    "targetport",
    "name",
    "component",
    "environment",
}

DEFAULT_DNS_KEYS = {"name", "dns_name", "hosts", "record_type", "type", "data", "value", "zone"}


class RegistryCollector:
    """Orchestrates registry sync and DB refresh."""

    def __init__(self):
        self.repository = RegistryRepository()
        self.git_sync: Optional[RegistryGitlabSync] = None
        self.linker = RegistryLinker(self.repository)
        self.cache = RegistryCacheService()
        self.manual_service = RegistryManualService(self.repository, self.cache)
        self._lock_token: Optional[str] = None
        self._lock = threading.Lock()
        self._mismatch_summary_lock = threading.Lock()

    def _get_git_sync(self) -> RegistryGitlabSync:
        if self.git_sync is None:
            self.git_sync = RegistryGitlabSync()
        return self.git_sync

    def _current_registry_config(self) -> Dict[str, Any]:
        current_config = get_app_config()
        if not current_config:
            return {}
        registry_config = current_config.get_addresses_monitor_config()
        return registry_config if isinstance(registry_config, dict) else {}

    def _monitor_config(self) -> Dict[str, Any]:
        return self._current_registry_config()

    def _exclude_internal_load_balancers(self) -> bool:
        current_config = get_app_config()
        if current_config:
            return bool(getattr(current_config, "registry_exclude_internal_load_balancers", False))
        return False

    def _target_summary(self, *, status: str, last_update: Optional[datetime] = None) -> MonitoringTargetSummary:
        current_config = get_app_config()
        target = current_config.get_targets_by_type("addresses")[0] if current_config else None
        if not current_config or not target:
            raise RuntimeError("Addresses target is not configured")
        return MonitoringTargetSummary(
            id="addresses:addresses",
            name=target.name,
            type="addresses",
            status=status,
            last_update=last_update,
            group=None,
        )

    def _lock_ttl(self) -> int:
        current_config = get_app_config()
        if current_config and getattr(current_config, "registry_refresh_interval_s", None):
            return max(900, int(current_config.registry_refresh_interval_s) * 2)
        return REGISTRY_COLLECTOR_LOCK_TTL_S

    @contextmanager
    def _collector_lock(self):
        lock_key = REGISTRY_COLLECTOR_LOCK_KEY
        token = f"{datetime.now(timezone.utc).timestamp()}-{threading.get_ident()}"
        acquired = False
        try:
            acquired = bool(
                self.cache.cache.redis_client.set(
                    lock_key,
                    token,
                    ex=self._lock_ttl(),
                    nx=True,
                )
            )
            yield acquired
        finally:
            if not acquired:
                return
            try:
                release_script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("del", KEYS[1])
                else
                    return 0
                end
                """
                self.cache.cache.redis_client.eval(release_script, 1, lock_key, token)
            except Exception as exc:
                logger.warning("Failed to release registry collector lock: %s", exc)

    @staticmethod
    def _as_list(value: Any) -> List[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]

    @staticmethod
    def _to_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return str(value)

    @staticmethod
    def _extract_scalar(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        return None

    @staticmethod
    def _normalize_ip(value: Any) -> Optional[str]:
        text = RegistryCollector._to_text(value)
        if not text:
            return None
        try:
            return str(ipaddress.ip_address(text))
        except ValueError:
            return None

    def _is_internal_lb_mapping(self, node: Dict[str, Any]) -> bool:
        if not isinstance(node, dict):
            return False
        if not self._exclude_internal_load_balancers():
            return False

        annotations = node.get("annotations")
        if not isinstance(annotations, dict):
            return False

        lb_type = RegistryCollector._to_text(annotations.get("yandex.cloud/load-balancer-type"))
        if lb_type and lb_type.strip().lower() == "internal":
            return True

        return False

    @staticmethod
    def _safe_path(file_path: Path, repo_root: Path) -> str:
        try:
            return str(file_path.relative_to(repo_root)).replace("\\", "/")
        except Exception:
            return str(file_path).replace("\\", "/")

    @staticmethod
    def _is_templated_dns(value: str) -> bool:
        return "${" in value and "}" in value

    @staticmethod
    def _normalize_dns_fqdn(name: str, zone: Optional[str]) -> str:
        dns_name = (name or "").strip().rstrip(".")
        dns_zone = (zone or "").strip().rstrip(".")
        if not dns_name:
            return ""
        if RegistryCollector._is_templated_dns(dns_name):
            return ""
        if not dns_zone:
            return dns_name
        if dns_name == "@":
            return dns_zone
        if dns_name.endswith(dns_zone):
            return dns_name
        return f"{dns_name}.{dns_zone}"

    @staticmethod
    def _read_dns_zone_file(directory: Path) -> Optional[str]:
        zone_file = directory / "zone.tf"
        if not zone_file.exists():
            return None
        try:
            content = zone_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None
        match = re.search(r'(?m)^\s*zone\s*=\s*"([^"]+)"', content)
        if not match:
            return None
        zone = match.group(1).strip().strip(".")
        return zone or None

    @staticmethod
    def _infer_dns_zone_from_path(file_path: Path) -> Optional[str]:
        for directory in (file_path.parent, *file_path.parents):
            if directory.name.startswith("dns_"):
                zone = RegistryCollector._read_dns_zone_file(directory)
                if zone:
                    return zone
                fallback_zone = directory.name[len("dns_"):].strip().strip(".").replace("-", ".")
                return fallback_zone or None
        return None

    @staticmethod
    def _build_source_file_url(reference: Dict[str, Any]) -> Optional[str]:
        repo_url = RegistryCollector._to_text(reference.get("repo_url"))
        file_path = RegistryCollector._to_text(reference.get("file_path"))
        if not repo_url or not file_path:
            return None

        base_url = repo_url[:-4] if repo_url.endswith(".git") else repo_url
        ref = (
            RegistryCollector._to_text(reference.get("repo_commit"))
            or RegistryCollector._to_text(reference.get("repo_ref"))
            or "HEAD"
        )
        encoded_ref = quote(ref, safe="")
        normalized_path = "/".join(quote(part, safe="._-") for part in file_path.split("/"))
        url = f"{base_url}/-/blob/{encoded_ref}/{normalized_path}"

        line_start = reference.get("source_line_start")
        line_end = reference.get("source_line_end")
        try:
            if line_start is not None:
                start = int(line_start)
                if line_end is not None and int(line_end) != start:
                    url += f"#L{start}-{int(line_end)}"
                else:
                    url += f"#L{start}"
        except (TypeError, ValueError):
            pass
        return url

    @staticmethod
    def _load_yaml_documents(content: str) -> List[Any]:
        docs = []
        try:
            for doc in yaml.safe_load_all(content):
                if doc is not None:
                    docs.append(doc)
        except Exception:
            pass
        return docs

    @staticmethod
    def _flatten_patterns(patterns: Any) -> List[str]:
        result: List[str] = []
        for item in RegistryCollector._as_list(patterns):
            text = RegistryCollector._to_text(item)
            if text:
                result.append(text)
        return result

    @staticmethod
    def _normalize_rules(registry_config: Dict[str, Any], extractor_name: str) -> List[Dict[str, Any]]:
        rules = []
        raw_rules = registry_config.get("extractionRules") or []
        for rule in raw_rules:
            if not isinstance(rule, dict):
                continue
            rule_extractor = str(rule.get("extractor") or "").strip() or extractor_name
            if rule_extractor != extractor_name:
                continue
            match_keys = {
                str(key).strip().lower()
                for key in RegistryCollector._as_list(rule.get("matchKeys"))
                if RegistryCollector._to_text(key)
            }
            match_patterns = RegistryCollector._flatten_patterns(rule.get("matchPatterns"))
            rules.append(
                {
                    "name": str(rule.get("name") or extractor_name),
                    "extractor": rule_extractor,
                    "match_keys": match_keys,
                    "match_patterns": match_patterns,
                }
            )
        if not rules:
            if extractor_name == "helm-yaml":
                rules.append(
                    {
                        "name": "helm-yaml",
                        "extractor": extractor_name,
                        "match_keys": set(DEFAULT_SERVICE_KEYS),
                        "match_patterns": [],
                    }
                )
            elif extractor_name == "terraform-hcl":
                rules.append(
                    {
                        "name": "terraform-hcl",
                        "extractor": extractor_name,
                        "match_keys": {"records", "name", "type", "data"},
                        "match_patterns": [],
                    }
                )
            elif extractor_name == "text-snippet":
                rules.append(
                    {
                        "name": "text-snippet",
                        "extractor": extractor_name,
                        "match_keys": set(),
                        "match_patterns": [],
                    }
                )
        return rules

    def _should_scan_file(self, repo_kind: str, file_path: Path) -> bool:
        suffix = file_path.suffix.lower()
        if repo_kind == "terraform-hcl":
            return suffix in {".tf", ".hcl", ".tfvars", ".json", ".yaml", ".yml"}
        if repo_kind == "helm-yaml":
            return suffix in {".yaml", ".yml", ".json", ".tpl", ".txt"}
        return suffix in {".yaml", ".yml", ".json", ".tf", ".hcl", ".tfvars", ".txt", ".tpl"}

    def _extract_yaml_objects(
        self,
        *,
        repo_cfg: Dict[str, Any],
        repo_root: Path,
        repo_commit: Optional[str],
        file_path: Path,
        content: str,
        rule: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        extracted: List[Dict[str, Any]] = []
        docs = self._load_yaml_documents(content)

        def walk(node: Any, path: List[str], doc_index: int) -> None:
            if isinstance(node, dict):
                direct_keys = {str(key).strip().lower() for key in node.keys()}
                if direct_keys & rule["match_keys"]:
                    attrs = self._yaml_attrs_from_mapping(node, rule)
                    if attrs:
                        extracted.append(
                            self._build_source_object(
                                repo_cfg=repo_cfg,
                                repo_root=repo_root,
                                repo_commit=repo_commit,
                                file_path=file_path,
                                object_type=rule["name"],
                                extractor_type=rule["extractor"],
                                object_key=":".join([self._safe_path(file_path, repo_root), str(doc_index), *path]),
                                node=node,
                                attrs=attrs,
                            )
                        )
                for key, value in node.items():
                    walk(value, path + [str(key)], doc_index)
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, path + [str(index)], doc_index)

        for doc_index, doc in enumerate(docs):
            walk(doc, [], doc_index)
        return extracted

    def _yaml_attrs_from_mapping(self, node: Dict[str, Any], rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self._is_internal_lb_mapping(node):
            return []

        attrs: List[Dict[str, Any]] = []
        for key, value in node.items():
            key_lower = str(key).strip().lower()
            if key_lower in {"loadbalancerip", "externalips", "ip", "address"}:
                for item in self._as_list(value):
                    text = self._normalize_ip(item)
                    if text and classify_registry_address(text, source_node=node):
                        attrs.append({"attr_name": key, "attr_type": "ip", "attr_value": text, "is_primary": True})
            elif key_lower in {"hosts", "dns_name", "hostname", "host"}:
                for item in self._as_list(value):
                    text = self._to_text(item)
                    if text:
                        attrs.append({"attr_name": key, "attr_type": "dns", "attr_value": text, "is_primary": True})
            elif key_lower in {"port", "targetport"}:
                for item in self._as_list(value):
                    text = self._to_text(item)
                    if text:
                        attrs.append({"attr_name": key, "attr_type": "port", "attr_value": text, "is_primary": True})
            elif key_lower in {"protocol"}:
                text = self._to_text(value)
                if text:
                    attrs.append({"attr_name": key, "attr_type": "protocol", "attr_value": text.upper(), "is_primary": True})
            elif key_lower in {"namespace", "service_name", "servicename", "fullnameoverride", "nameoverride", "component", "environment"}:
                text = self._to_text(value)
                if text:
                    attrs.append({"attr_name": key, "attr_type": key_lower, "attr_value": text, "is_primary": True})
            elif key_lower in {"ports"} and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        attrs.extend(self._yaml_attrs_from_mapping(item, rule))
                        port_value = item.get("port")
                        if port_value is not None:
                            text = self._to_text(port_value)
                            if text:
                                attrs.append({"attr_name": "port", "attr_type": "port", "attr_value": text, "is_primary": True, "position": index})
                        target_port = item.get("targetPort")
                        if target_port is not None:
                            text = self._to_text(target_port)
                            if text:
                                attrs.append({"attr_name": "targetPort", "attr_type": "port", "attr_value": text, "is_primary": False, "position": index})
            elif isinstance(value, str) and key_lower in rule["match_keys"]:
                attrs.append({"attr_name": key, "attr_type": "raw", "attr_value": value, "is_primary": False})
        return attrs

    def _extract_text_objects(
        self,
        *,
        repo_cfg: Dict[str, Any],
        repo_root: Path,
        repo_commit: Optional[str],
        file_path: Path,
        content: str,
        rule: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        patterns = rule.get("match_patterns") or []
        if not patterns:
            return []
        lines = content.splitlines()
        matched_lines: List[Tuple[int, str]] = []
        for line_no, line in enumerate(lines, start=1):
            for pattern in patterns:
                try:
                    if re.search(pattern, line):
                        matched_lines.append((line_no, line))
                        break
                except re.error:
                    if pattern in line:
                        matched_lines.append((line_no, line))
                        break
        if not matched_lines:
            return []
        start_line = max(1, matched_lines[0][0] - 2)
        end_line = min(len(lines), matched_lines[-1][0] + 2)
        raw_excerpt = "\n".join(lines[start_line - 1 : end_line])
        attrs = [
            {
                "attr_name": "raw",
                "attr_type": "raw",
                "attr_value": raw_excerpt,
                "is_primary": True,
                "line_no": line_no,
                "position": idx,
            }
            for idx, (line_no, _line) in enumerate(matched_lines)
        ]
        return [
            self._build_source_object(
                repo_cfg=repo_cfg,
                repo_root=repo_root,
                repo_commit=repo_commit,
                file_path=file_path,
                object_type=rule["name"],
                extractor_type=rule["extractor"],
                object_key=":".join([self._safe_path(file_path, repo_root), rule["name"]]),
                node=None,
                attrs=attrs,
                source_line_start=start_line,
                source_line_end=end_line,
            )
        ]

    def _extract_hcl_objects(
        self,
        *,
        repo_cfg: Dict[str, Any],
        repo_root: Path,
        repo_commit: Optional[str],
        file_path: Path,
        content: str,
        rule: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if hcl2 is None:
            return []
        try:
            parsed = hcl2.loads(content)
        except Exception:
            return []

        extracted: List[Dict[str, Any]] = []

        def walk(node: Any, path: List[str], module_context: List[str]) -> None:
            if isinstance(node, dict):
                if "records" in node and isinstance(node.get("records"), list):
                    for record_index, record in enumerate(node["records"]):
                        if not isinstance(record, dict):
                            continue
                        attrs = self._hcl_record_attrs(record, node, module_context, rule)
                        if attrs:
                            extracted.append(
                                self._build_source_object(
                                    repo_cfg=repo_cfg,
                                    repo_root=repo_root,
                                    repo_commit=repo_commit,
                                    file_path=file_path,
                                    object_type=rule["name"],
                                    extractor_type=rule["extractor"],
                                    object_key=":".join([self._safe_path(file_path, repo_root), *path, "records", str(record_index)]),
                                    node=record,
                                    attrs=attrs,
                                )
                            )
                if {"name", "type", "data"} & {str(key).lower() for key in node.keys()}:
                    is_nested_record = len(path) >= 2 and path[-2] == "records"
                    if not is_nested_record:
                        attrs = self._hcl_record_attrs(node, {}, module_context, rule)
                        if attrs:
                            extracted.append(
                                self._build_source_object(
                                    repo_cfg=repo_cfg,
                                    repo_root=repo_root,
                                    repo_commit=repo_commit,
                                    file_path=file_path,
                                    object_type=rule["name"],
                                    extractor_type=rule["extractor"],
                                    object_key=":".join([self._safe_path(file_path, repo_root), *path, "record"]),
                                    node=node,
                                    attrs=attrs,
                                )
                            )
                next_module_context = list(module_context)
                if "module" in node and isinstance(node["module"], dict):
                    next_module_context.extend(
                        str(key)
                        for key in node["module"].keys()
                    )
                for key, value in node.items():
                    walk(value, path + [str(key)], next_module_context)
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, path + [str(index)], module_context)

        walk(parsed, [], [])
        return extracted

    def _hcl_record_attrs(
        self,
        record: Dict[str, Any],
        parent: Dict[str, Any],
        module_context: List[str],
        rule: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        attrs: List[Dict[str, Any]] = []
        if not isinstance(record, dict):
            return attrs
        zone = record.get("zone") or parent.get("zone")
        zone_text = self._to_text(zone)
        name = record.get("name")
        if name is not None:
            text = self._to_text(name)
            if text:
                fqdn = self._normalize_dns_fqdn(text, zone_text)
                dns_value = fqdn or text
                if not self._is_templated_dns(dns_value):
                    attrs.append({"attr_name": "dns_name", "attr_type": "dns", "attr_value": dns_value, "is_primary": True})
        record_type = record.get("type") or record.get("record_type")
        if record_type is not None:
            text = self._to_text(record_type)
            if text:
                attrs.append({"attr_name": "record_type", "attr_type": "record_type", "attr_value": text.upper(), "is_primary": True})
        data = record.get("data")
        if data is not None:
            for item in self._as_list(data):
                text = self._to_text(item)
                if text:
                    normalized_ip = self._normalize_ip(text)
                    if normalized_ip:
                        if classify_registry_address(normalized_ip):
                            attrs.append({"attr_name": "data", "attr_type": "ip", "attr_value": normalized_ip, "is_primary": True})
                    else:
                        if not self._is_templated_dns(text):
                            attrs.append({"attr_name": "data", "attr_type": "dns", "attr_value": text, "is_primary": True})
        if zone is not None:
            if zone_text:
                attrs.append({"attr_name": "zone", "attr_type": "zone", "attr_value": zone_text, "is_primary": False})
        if module_context:
            attrs.append(
                {
                    "attr_name": "module_context",
                    "attr_type": "raw",
                    "attr_value": "/".join(module_context),
                    "is_primary": False,
                }
            )
        return attrs

    def _build_source_object(
        self,
        *,
        repo_cfg: Dict[str, Any],
        repo_root: Path,
        repo_commit: Optional[str],
        file_path: Path,
        object_type: str,
        extractor_type: str,
        object_key: str,
        node: Optional[Dict[str, Any]],
        attrs: List[Dict[str, Any]],
        source_line_start: Optional[int] = None,
        source_line_end: Optional[int] = None,
    ) -> Dict[str, Any]:
        collected_at = datetime.now(timezone.utc)
        service_name = None
        environment = None
        component = None
        namespace = None
        for attr in attrs:
            attr_name = str(attr.get("attr_name") or "").strip().lower()
            value = self._to_text(attr.get("attr_value"))
            if not value:
                continue
            if attr_name in {"service_name", "servicename", "fullnameoverride", "nameoverride", "name"} and service_name is None:
                service_name = value
            elif attr_name in {"environment"} and environment is None:
                environment = value
            elif attr_name in {"component", "release_name"} and component is None:
                component = value
            elif attr_name in {"namespace"} and namespace is None:
                namespace = value

        if node and service_name is None:
            for key in ("serviceName", "fullnameOverride", "nameOverride", "name"):
                value = self._to_text(node.get(key))
                if value:
                    service_name = value
                    break
        if node and component is None:
            component = self._to_text(node.get("component")) or component
        if node and environment is None:
            environment = self._to_text(node.get("environment")) or environment
        if node and namespace is None:
            namespace = self._to_text(node.get("namespace")) or namespace

        if service_name is None and extractor_type == "helm-yaml":
            file_name = file_path.name
            if file_name == "values.yaml":
                service_name = file_path.parent.name
            elif file_name.startswith("values-") and "." in file_name:
                service_name = file_name[len("values-"):].rsplit(".", 1)[0]
            elif file_name.endswith((".yaml", ".yml")):
                service_name = file_name.rsplit(".", 1)[0]

        if extractor_type == "terraform-hcl":
            inferred_zone = self._infer_dns_zone_from_path(file_path)
            if inferred_zone:
                has_zone_attr = any(str(attr.get("attr_name") or "").strip().lower() == "zone" for attr in attrs)
                for attr in attrs:
                    attr_name = str(attr.get("attr_name") or "").strip().lower()
                    value = self._to_text(attr.get("attr_value"))
                    if not value:
                        continue
                    if attr_name == "dns_name":
                        normalized_value = self._normalize_dns_fqdn(value, inferred_zone)
                        if normalized_value:
                            attr["attr_value"] = normalized_value
                if not has_zone_attr:
                    attrs.append({"attr_name": "zone", "attr_type": "zone", "attr_value": inferred_zone, "is_primary": False})

        return {
            "repo_name": repo_cfg.get("name") or "registry-repo",
            "repo_url": repo_cfg.get("url") or "",
            "repo_ref": repo_cfg.get("ref") or "HEAD",
            "repo_commit": repo_commit,
            "file_path": self._safe_path(file_path, repo_root),
            "object_key": object_key,
            "object_type": object_type,
            "extractor_type": extractor_type,
            "environment": environment,
            "component": component,
            "service_name": service_name,
            "namespace": namespace,
            "source_line_start": source_line_start,
            "source_line_end": source_line_end,
            "collected_at": collected_at,
            "attributes": attrs,
        }

    def _scan_repo(self, repo_cfg: Dict[str, Any], repo_dir: Path, repo_commit: Optional[str]) -> List[Dict[str, Any]]:
        repo_kind = str(repo_cfg.get("kind") or "").strip().lower()
        include_paths = repo_cfg.get("includePaths") or []
        include_paths = [Path(path) for path in include_paths] if include_paths else [Path(".")]
        registry_config = self._monitor_config()
        yaml_rules = self._normalize_rules(registry_config, "helm-yaml")
        hcl_rules = self._normalize_rules(registry_config, "terraform-hcl")
        text_rules = self._normalize_rules(registry_config, "text-snippet")
        extracted: List[Dict[str, Any]] = []

        for base_path in include_paths:
            scan_root = (repo_dir / base_path).resolve()
            if not scan_root.exists():
                continue
            if scan_root.is_file():
                candidates = [scan_root]
            else:
                candidates = [path for path in scan_root.rglob("*") if path.is_file() and ".git" not in path.parts]
            for file_path in candidates:
                if not self._should_scan_file(repo_kind, file_path):
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if file_path.suffix.lower() in {".yaml", ".yml", ".json", ".tpl"} and yaml_rules:
                    for rule in yaml_rules:
                        extracted.extend(
                            self._extract_yaml_objects(
                                repo_cfg=repo_cfg,
                                repo_root=repo_dir,
                                repo_commit=repo_commit,
                                file_path=file_path,
                                content=content,
                                rule=rule,
                            )
                        )
                if file_path.suffix.lower() in {".tf", ".hcl", ".tfvars", ".json"} and hcl_rules:
                    for rule in hcl_rules:
                        extracted.extend(
                            self._extract_hcl_objects(
                                repo_cfg=repo_cfg,
                                repo_root=repo_dir,
                                repo_commit=repo_commit,
                                file_path=file_path,
                                content=content,
                                rule=rule,
                            )
                        )
                if text_rules and any(pattern for rule in text_rules for pattern in rule.get("match_patterns") or []):
                    for rule in text_rules:
                        extracted.extend(
                            self._extract_text_objects(
                                repo_cfg=repo_cfg,
                                repo_root=repo_dir,
                                repo_commit=repo_commit,
                                file_path=file_path,
                                content=content,
                                rule=rule,
                            )
                        )
        return extracted

    def collect_registry(self) -> RegistryCollectResponse:
        """Perform one addresses collection pass."""
        current_config = get_app_config()
        registry_config = self._monitor_config()
        if not current_config:
            raise RuntimeError("App config is not initialized")
        repos = registry_config.get("repos") or []
        if not isinstance(repos, list) or not repos:
            raise RuntimeError("Addresses monitor has no configured repositories")

        with self._collector_lock() as acquired:
            if not acquired:
                summary = self.repository.get_summary(current_config.registry_refresh_interval_s)
                dashboard = self._build_dashboard_payload(
                    target_name="addresses",
                    status="busy",
                    summary=summary,
                )
                return RegistryCollectResponse(
                    **dashboard.model_dump(),
                    queued=False,
                    message="Addresses collection is already running",
                )

            self.repository.ensure_schema()
            repo_results: List[Dict[str, Any]] = []
            any_success = False
            any_failure = False
            for repo_cfg in repos:
                if not isinstance(repo_cfg, dict):
                    continue
                sync_result = self._get_git_sync().sync_repo(repo_cfg)
                details = dict(sync_result.details or {})
                run_id = self.repository.record_sync_run(
                    repo_name=sync_result.repo_name,
                    repo_url=sync_result.repo_url,
                    repo_ref=sync_result.repo_ref,
                    repo_commit=sync_result.repo_commit,
                    status=sync_result.status,
                    started_at=sync_result.started_at,
                    finished_at=sync_result.finished_at,
                    details=details,
                )
                if sync_result.status == "success":
                    any_success = True
                    source_rows = self._scan_repo(repo_cfg, Path(sync_result.repo_dir), sync_result.repo_commit)
                    self.repository.replace_source_objects(
                        repo_name=sync_result.repo_name,
                        source_rows=source_rows,
                        run_id=run_id,
                    )
                else:
                    any_failure = True
                repo_results.append(
                    {
                        "repo_name": sync_result.repo_name,
                        "repo_ref": sync_result.repo_ref,
                        "repo_commit": sync_result.repo_commit,
                        "status": sync_result.status,
                        "last_synced_at": sync_result.finished_at,
                        "error": sync_result.error,
                    }
                )

            linked_rows = self.linker.refresh_linked_rows()
            validated_rows = self._validate_dns_rows(linked_rows)
            self.repository.sync_dns_history("auto_gitlab", validated_rows, datetime.now(timezone.utc))
            validated_rows = self.repository.enrich_rows_with_dns_history("auto_gitlab", validated_rows)
            summary_data = self.repository.get_summary(current_config.registry_refresh_interval_s)
            status = "ready"
            if any_failure and any_success:
                status = "partial_failure"
            elif any_failure and not any_success:
                status = "failed"
            last_update = datetime.now(timezone.utc)
            summary_data["repos"] = repo_results or summary_data.get("repos", [])
            summary_data["last_sync_status"] = status
            summary_data["last_sync_at"] = last_update
            self.repository.upsert_monitor_state(status=status, last_update=last_update, summary=summary_data)
            target_summary = self._target_summary(status=status, last_update=last_update)
            self.cache.set_state(
                {
                    "status": status,
                    "last_update": last_update,
                    "summary": summary_data,
                    "target": target_summary.model_dump(),
                }
            )
            dashboard = self._build_dashboard_payload(
                target_name="addresses",
                status=status,
                summary=summary_data,
                auto_rows_data=validated_rows,
            )
            self.cache.set_dashboard_payload(self._dashboard_snapshot(dashboard))
            self.cache.delete_mismatch_summary()
            return RegistryCollectResponse(
                **dashboard.model_dump(),
                queued=False,
                message=f"Addresses collection completed: {len(linked_rows)} linked rows",
            )

    def _build_dashboard_payload(
        self,
        *,
        target_name: str,
        status: str,
        summary: Dict[str, Any],
        auto_rows_data: Optional[List[Dict[str, Any]]] = None,
    ) -> RegistryDashboardResponse:
        cache_state = self.cache.get_state() or {}
        manual_rows = [
            row.model_copy(
                update={
                    "address_scope": classify_registry_address(row.ip_or_cidr or row.ip) or "external",
                }
            )
            for row in self.manual_service.list_rows()
        ]
        rows_source = auto_rows_data if auto_rows_data is not None else self.repository.enrich_rows_with_dns_history(
            "auto_gitlab",
            self.repository.list_linked_rows(),
        )
        auto_rows = [
            RegistryAutoRow(
                ip=row.get("ip") or "",
                address_scope=classify_registry_address(row.get("ip")) or "external",
                service_name=row.get("service_name"),
                dns_name=row.get("dns_name"),
                dns_names=row.get("dns_names") or ([row.get("dns_name")] if row.get("dns_name") else []),
                dns_validation=RegistryDnsValidation(**row.get("dns_validation")) if row.get("dns_validation") else None,
                previous_ips=row.get("previous_ips") or [],
                ip_changed_at=row.get("ip_changed_at"),
                protocol=row.get("protocol"),
                port=row.get("port"),
                origin=row.get("origin") or "auto_gitlab",
                environment=row.get("environment"),
                component=row.get("component"),
                source_references=[
                    RegistrySourceReference(
                        **{
                            **ref,
                            "source_file_url": ref.get("source_file_url") or self._build_source_file_url(ref),
                        }
                    )
                    for ref in (row.get("source_references") or [])
                ],
            )
            for row in rows_source
        ]
        summary_payload = dict(summary)
        summary_payload["auto_rows_count"] = len(auto_rows)
        summary_payload["linked_rows_count"] = len(auto_rows)
        summary_payload["total_linked_rows"] = len(auto_rows)
        summary_payload["manual_rows_count"] = len(manual_rows)
        summary_payload["total_manual_rows"] = len(manual_rows)
        current_config = get_app_config()
        view_summaries = []
        for view in (current_config.get_addresses_view_configs() if current_config else []):
            view_name = str(view.get("name") or "").strip().lower()
            if not view_name:
                continue
            auto_count = sum(1 for row in auto_rows if row.address_scope == view_name)
            manual_count = sum(1 for row in manual_rows if row.address_scope == view_name)
            view_summaries.append(
                {
                    "name": view_name,
                    "label": view.get("label") or view_name,
                    "auto_rows_count": auto_count,
                    "manual_rows_count": manual_count,
                    "total_rows_count": auto_count + manual_count,
                }
            )
        summary_payload["views"] = view_summaries
        last_update = summary.get("last_sync_at") or cache_state.get("last_update")
        if isinstance(last_update, str):
            try:
                last_update = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
            except Exception:
                last_update = None
        target_summary = self._target_summary(status=status, last_update=last_update)
        return RegistryDashboardResponse(
            target=target_summary,
            status=status,
            last_update=last_update,
            summary=RegistrySummary(**summary_payload),
            auto_rows=auto_rows,
            manual_rows=manual_rows,
        )

    def _validate_dns_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        validated_rows: List[Dict[str, Any]] = []
        checked_at = datetime.now(timezone.utc).isoformat()
        for row in rows:
            row_copy = dict(row)
            expected_ip = self._normalize_ip(row_copy.get("ip"))
            dns_names = [
                str(item).strip()
                for item in (row_copy.get("dns_names") or ([row_copy.get("dns_name")] if row_copy.get("dns_name") else []))
                if str(item).strip()
            ]
            if not expected_ip or not dns_names:
                row_copy["dns_validation"] = {
                    "status": "skipped",
                    "resolved_ips": [],
                    "checked_at": checked_at,
                    "message": "DNS check skipped",
                }
                validated_rows.append(row_copy)
                continue

            resolved_ips: List[str] = []
            resolution_errors: List[str] = []
            skipped_reasons: List[str] = []
            for dns_name in dns_names:
                if "*" in dns_name:
                    skipped_reasons.append(f"Wildcard DNS skipped: {dns_name}")
                    continue
                if "${" in dns_name and "}" in dns_name:
                    skipped_reasons.append(f"Templated DNS skipped: {dns_name}")
                    continue
                try:
                    infos = socket.getaddrinfo(dns_name.rstrip("."), None, proto=socket.IPPROTO_TCP)
                    for info in infos:
                        sockaddr = info[4]
                        if not sockaddr:
                            continue
                        normalized_ip = self._normalize_ip(sockaddr[0])
                        if normalized_ip and normalized_ip not in resolved_ips:
                            resolved_ips.append(normalized_ip)
                except socket.gaierror as exc:
                    resolution_errors.append(f"{dns_name}: {exc}")

            if expected_ip in resolved_ips:
                status_value = "ok"
                message = None
            elif resolved_ips:
                status_value = "mismatch"
                message = f"Expected {expected_ip}, resolved {', '.join(resolved_ips)}"
            elif resolution_errors:
                status_value = "unresolved"
                message = "; ".join(resolution_errors[:3])
            elif skipped_reasons:
                status_value = "skipped"
                message = "; ".join(skipped_reasons[:3])
            else:
                status_value = "skipped"
                message = "DNS check skipped"

            row_copy["dns_validation"] = {
                "status": status_value,
                "resolved_ips": resolved_ips,
                "checked_at": checked_at,
                "message": message,
            }
            validated_rows.append(row_copy)
        return validated_rows

    @staticmethod
    def _dashboard_snapshot(payload: RegistryDashboardResponse) -> Dict[str, Any]:
        return {
            "status": payload.status,
            "last_update": payload.last_update,
            "summary": payload.summary.model_dump(mode="json"),
            "target": payload.target.model_dump(mode="json"),
            "auto_rows": [row.model_dump(mode="json") for row in payload.auto_rows],
        }

    def get_dashboard_payload(self) -> RegistryDashboardResponse:
        """Read current registry state."""
        current_config = get_app_config()
        if not current_config:
            raise RuntimeError("App config is not initialized")
        registry_config = current_config.get_addresses_monitor_config()
        if not registry_config or not registry_config.get("repos"):
            raise RuntimeError("Addresses monitor is not configured")
        cached_payload = self.cache.get_dashboard_payload() or {}
        cached_auto_rows = cached_payload.get("auto_rows")
        cached_summary = cached_payload.get("summary")
        cached_status = cached_payload.get("status")
        if isinstance(cached_auto_rows, list) and isinstance(cached_summary, dict):
            return self._build_dashboard_payload(
                target_name="addresses",
                status=cached_status or cached_summary.get("last_sync_status") or "ready",
                summary=cached_summary,
                auto_rows_data=cached_auto_rows,
            )
        summary = self.repository.get_summary(current_config.registry_refresh_interval_s)
        monitor_state = self.repository.get_monitor_state() or {}
        cache_state = self.cache.get_state() or {}
        status = (
            monitor_state.get("status")
            or cache_state.get("status")
            or ("ready" if summary.get("total_source_objects") else "warming_up")
        )
        payload = self._build_dashboard_payload(
            target_name="addresses",
            status=status,
            summary=summary,
            auto_rows_data=self.repository.enrich_rows_with_dns_history(
                "auto_gitlab",
                self._validate_dns_rows(self.repository.list_linked_rows()),
            ),
        )
        self.cache.set_dashboard_payload(self._dashboard_snapshot(payload))
        return payload

    def get_mismatch_summary(self) -> RegistryMismatchSummary:
        cached_summary = self.cache.get_mismatch_summary()
        if isinstance(cached_summary, dict):
            try:
                return RegistryMismatchSummary(**cached_summary)
            except Exception:
                pass

        with self._mismatch_summary_lock:
            cached_summary = self.cache.get_mismatch_summary()
            if isinstance(cached_summary, dict):
                try:
                    return RegistryMismatchSummary(**cached_summary)
                except Exception:
                    pass

            cached_payload = self.cache.get_dashboard_payload() or {}
            cached_auto_rows = cached_payload.get("auto_rows") if isinstance(cached_payload, dict) else None
            if isinstance(cached_auto_rows, list):
                auto_rows = cached_auto_rows
            else:
                auto_rows = self._validate_dns_rows(
                    self.repository.enrich_rows_with_dns_history(
                        "auto_gitlab",
                        self.repository.list_linked_rows(),
                    )
                )

            manual_rows = [row.model_dump(mode="json") for row in self.manual_service.list_rows()]
            auto_count = sum(
                1
                for row in auto_rows
                if isinstance(row, dict) and (row.get("dns_validation") or {}).get("status") == "mismatch"
            )
            manual_count = sum(
                1
                for row in manual_rows
                if isinstance(row, dict) and (row.get("dns_validation") or {}).get("status") == "mismatch"
            )
            summary = RegistryMismatchSummary(
                total_count=auto_count + manual_count,
                auto_count=auto_count,
                manual_count=manual_count,
            )
            self.cache.set_mismatch_summary(summary.model_dump(mode="json"))
            return summary

    def collect(self) -> Dict[str, Any]:
        """Compatibility wrapper used by existing tasks."""
        result = self.collect_registry()
        return result.model_dump()


registry_collector = RegistryCollector()
