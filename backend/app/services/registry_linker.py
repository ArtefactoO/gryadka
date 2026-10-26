"""
Registry linker: converts extracted source objects into final auto rows.
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.services.registry_repository import RegistryRepository

logger = logging.getLogger(__name__)


@dataclass
class RegistryNormalizedSource:
    source_object_id: int
    repo_name: str
    repo_url: str
    repo_ref: str
    repo_commit: Optional[str]
    file_path: str
    object_key: str
    object_type: str
    extractor_type: str
    service_name: Optional[str]
    environment: Optional[str]
    component: Optional[str]
    protocol: Optional[str]
    port: Optional[int]
    display_ips: List[str]
    linkable_ips: List[str]
    dns_names: List[str]
    is_dns_object: bool
    source_reference: Dict[str, Any]


class RegistryLinker:
    """Build linked rows from source objects and attributes."""

    def __init__(self, repository: Optional[RegistryRepository] = None):
        self.repository = repository or RegistryRepository()

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
    def _to_int(value: Any) -> Optional[int]:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_ip(value: Any) -> Optional[str]:
        text = RegistryLinker._to_text(value)
        if not text:
            return None
        if "/" in text:
            try:
                network = ipaddress.ip_network(text, strict=False)
                if network.num_addresses > 0:
                    return str(network.network_address)
            except Exception:
                return None
        try:
            return str(ipaddress.ip_address(text))
        except Exception:
            return None

    @staticmethod
    def _looks_like_dns(value: str) -> bool:
        if not value:
            return False
        if "${" in value and "}" in value:
            return False
        try:
            ipaddress.ip_address(value)
            return False
        except Exception:
            pass
        return "." in value or value.startswith("_")

    @staticmethod
    def _merge_unique_references(existing: List[Dict[str, Any]], new_refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: Set[Tuple[Any, ...]] = set()
        for ref in existing + new_refs:
            signature = (
                ref.get("repo_name"),
                ref.get("repo_commit"),
                ref.get("file_path"),
                ref.get("object_key"),
                ref.get("source_object_id"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            merged.append(ref)
        return merged

    @staticmethod
    def _merge_unique_strings(existing: List[str], new_values: List[str]) -> List[str]:
        merged: List[str] = []
        seen: Set[str] = set()
        for value in existing + new_values:
            text = (value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
        return merged

    @staticmethod
    def _display_dns_name(dns_names: List[str]) -> Optional[str]:
        normalized = [value for value in dns_names if value]
        if not normalized:
            return None
        ordered = sorted(normalized, key=lambda value: (len(value), value))
        primary = ordered[0]
        if len(ordered) == 1:
            return primary
        return f"{primary} (+{len(ordered) - 1})"

    def _normalize_source_object(self, source_object: Dict[str, Any]) -> RegistryNormalizedSource:
        attrs = source_object.get("attributes") or []
        display_ips: List[str] = []
        linkable_ips: List[str] = []
        dns_names: List[str] = []
        protocols: List[str] = []
        ports: List[int] = []
        service_names: List[str] = []
        environment: Optional[str] = None
        component: Optional[str] = None
        is_dns_object = False

        for attr in attrs:
            attr_name = str(attr.get("attr_name") or "").strip().lower()
            attr_type = str(attr.get("attr_type") or "").strip().lower()
            raw_value = attr.get("attr_value")
            values = self._as_list(raw_value)

            if attr_name in {"service_name", "service", "name", "fullnameoverride", "nameoverride"} or attr_type == "service_name":
                for item in values:
                    text = self._to_text(item)
                    if text:
                        service_names.append(text)
            if attr_name in {"environment", "env"} or attr_type == "namespace":
                environment = environment or self._to_text(raw_value)
            if attr_name in {"component", "release_name"} or attr_type == "component":
                component = component or self._to_text(raw_value)
            if attr_name in {"protocol"} or attr_type == "protocol":
                for item in values:
                    text = self._to_text(item)
                    if text:
                        protocols.append(text.upper())
            if attr_name in {"port", "targetport"} or attr_type == "port":
                for item in values:
                    port = self._to_int(item)
                    if port is not None:
                        ports.append(port)
            if attr_name in {"dns_name", "hostname", "host", "hosts"} or attr_type == "dns":
                for item in values:
                    text = self._to_text(item)
                    if text and self._looks_like_dns(text):
                        dns_names.append(text)
            if attr_name in {"record_type", "type"} or attr_type == "record_type":
                for item in values:
                    text = self._to_text(item)
                    if text and text.upper() in {"A", "AAAA"}:
                        is_dns_object = True
                    elif text:
                        is_dns_object = is_dns_object or False
            if attr_type == "ip" or attr_name in {"ip", "loadbalancerip", "externalips", "address"}:
                for item in values:
                    text = self._to_text(item)
                    if not text:
                        continue
                    display_ips.append(text)
                    normalized = self._normalize_ip(text)
                    if normalized:
                        linkable_ips.append(normalized)
            elif attr_type == "cidr":
                for item in values:
                    text = self._to_text(item)
                    if text:
                        display_ips.append(text)
        if not service_names:
            fallback_name = self._to_text(source_object.get("service_name"))
            if fallback_name:
                service_names.append(fallback_name)
        if not component:
            component = self._to_text(source_object.get("component"))
        if not environment:
            environment = self._to_text(source_object.get("environment"))

        service_name = service_names[0] if service_names else None
        protocol = protocols[0] if protocols else None
        port = ports[0] if ports else None

        source_reference = {
            "repo_name": source_object.get("repo_name"),
            "repo_url": source_object.get("repo_url"),
            "repo_ref": source_object.get("repo_ref"),
            "repo_commit": source_object.get("repo_commit"),
            "file_path": source_object.get("file_path"),
            "object_key": source_object.get("object_key"),
            "object_type": source_object.get("object_type"),
            "extractor_type": source_object.get("extractor_type"),
            "source_object_id": source_object.get("id"),
            "source_line_start": source_object.get("source_line_start"),
            "source_line_end": source_object.get("source_line_end"),
        }

        return RegistryNormalizedSource(
            source_object_id=int(source_object.get("id")),
            repo_name=str(source_object.get("repo_name") or ""),
            repo_url=str(source_object.get("repo_url") or ""),
            repo_ref=str(source_object.get("repo_ref") or ""),
            repo_commit=source_object.get("repo_commit"),
            file_path=str(source_object.get("file_path") or ""),
            object_key=str(source_object.get("object_key") or ""),
            object_type=str(source_object.get("object_type") or "unknown"),
            extractor_type=str(source_object.get("extractor_type") or "unknown"),
            service_name=service_name,
            environment=environment,
            component=component,
            protocol=protocol,
            port=port,
            display_ips=list(dict.fromkeys(display_ips)),
            linkable_ips=list(dict.fromkeys(linkable_ips)),
            dns_names=list(dict.fromkeys(dns_names)),
            is_dns_object=is_dns_object,
            source_reference=source_reference,
        )

    def build_linked_rows(self, source_objects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create linked rows from extracted source objects."""
        normalized_objects = [self._normalize_source_object(source_object) for source_object in source_objects]
        service_objects: List[RegistryNormalizedSource] = []
        dns_objects: List[RegistryNormalizedSource] = []
        for obj in normalized_objects:
            # Terraform records should only enrich existing infra rows with DNS names.
            # They must not generate standalone rows in the registry.
            if (obj.linkable_ips or obj.display_ips) and not obj.is_dns_object and obj.extractor_type != "terraform-hcl":
                service_objects.append(obj)
            if obj.is_dns_object or obj.dns_names:
                dns_objects.append(obj)

        dns_by_ip: Dict[str, List[RegistryNormalizedSource]] = {}
        for obj in dns_objects:
            for ip in obj.linkable_ips:
                dns_by_ip.setdefault(ip, []).append(obj)

        rows_by_signature: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        now = datetime.now(timezone.utc)

        for service_obj in service_objects:
            ips = service_obj.linkable_ips or service_obj.display_ips
            if not ips:
                continue
            for ip in ips:
                matched_dns = dns_by_ip.get(ip) or []
                if matched_dns:
                    for dns_obj in matched_dns:
                        row = self._build_row(service_obj, dns_obj, ip, now)
                        signature = self._row_signature(row)
                        existing = rows_by_signature.get(signature)
                        if existing is None:
                            rows_by_signature[signature] = row
                        else:
                            existing["_dns_names"] = self._merge_unique_strings(
                                existing.get("_dns_names") or [],
                                row.get("_dns_names") or [],
                            )
                            existing["dns_name"] = self._display_dns_name(existing["_dns_names"])
                            existing["source_object_ids"] = sorted(
                                set(existing.get("source_object_ids") or []) | set(row.get("source_object_ids") or [])
                            )
                            existing["source_references"] = self._merge_unique_references(
                                existing.get("source_references") or [],
                                row.get("source_references") or [],
                            )
                else:
                    row = self._build_row(service_obj, None, ip, now)
                    signature = self._row_signature(row)
                    existing = rows_by_signature.get(signature)
                    if existing is None:
                        rows_by_signature[signature] = row
                    else:
                        existing["_dns_names"] = self._merge_unique_strings(
                            existing.get("_dns_names") or [],
                            row.get("_dns_names") or [],
                        )
                        existing["dns_name"] = self._display_dns_name(existing["_dns_names"])
                        existing["source_object_ids"] = sorted(
                            set(existing.get("source_object_ids") or []) | set(row.get("source_object_ids") or [])
                        )
                        existing["source_references"] = self._merge_unique_references(
                            existing.get("source_references") or [],
                            row.get("source_references") or [],
                        )

        rows = list(rows_by_signature.values())
        for row in rows:
            dns_names = row.pop("_dns_names", [])
            row["dns_names"] = dns_names
            row["dns_name"] = self._display_dns_name(dns_names)
        rows.sort(key=lambda item: (item.get("ip") or "", item.get("service_name") or "", item.get("dns_name") or ""))
        return rows

    def _build_row(
        self,
        service_obj: RegistryNormalizedSource,
        dns_obj: Optional[RegistryNormalizedSource],
        ip: str,
        now: datetime,
    ) -> Dict[str, Any]:
        service_name = service_obj.service_name or service_obj.component or service_obj.object_key
        dns_name = None
        source_object_ids = [service_obj.source_object_id]
        source_references = [service_obj.source_reference]
        if dns_obj is not None:
            dns_name = dns_obj.dns_names[0] if dns_obj.dns_names else None
            if dns_name and self._normalize_ip(dns_name) == self._normalize_ip(ip):
                dns_name = None
            if dns_obj.source_object_id != service_obj.source_object_id:
                source_object_ids.append(dns_obj.source_object_id)
                source_references.append(dns_obj.source_reference)
        return {
            "source_object_ids": source_object_ids,
            "ip": ip,
            "service_name": service_name,
            "dns_name": dns_name,
            "_dns_names": [dns_name] if dns_name else [],
            "protocol": service_obj.protocol,
            "port": service_obj.port,
            "origin": "auto_gitlab",
            "environment": service_obj.environment,
            "component": service_obj.component,
            "source_references": source_references,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _row_signature(row: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            row.get("ip"),
            row.get("service_name"),
            row.get("protocol"),
            row.get("port"),
            row.get("origin"),
            row.get("environment"),
            row.get("component"),
        )

    def refresh_linked_rows(self) -> List[Dict[str, Any]]:
        """Rebuild linked rows from current source objects."""
        source_objects = self.repository.list_source_objects()
        rows = self.build_linked_rows(source_objects)
        self.repository.delete_linked_rows()
        self.repository.insert_linked_rows(rows)
        logger.info("Registry linker produced %s linked rows", len(rows))
        return rows
