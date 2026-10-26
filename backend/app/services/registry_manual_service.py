"""
Manual registry entry CRUD service.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import List, Optional

from app.config import get_app_config
from app.models.registry import (
    CreateManualRegistryEntryRequest,
    RegistryDnsValidation,
    RegistryManualRow,
    UpdateManualRegistryEntryRequest,
)
from app.services.registry_cache import RegistryCacheService
from app.services.registry_repository import RegistryRepository
from app.services.registry_scope import classify_registry_address


class RegistryManualService:
    def __init__(
        self,
        repository: Optional[RegistryRepository] = None,
        cache: Optional[RegistryCacheService] = None,
    ):
        self.repository = repository or RegistryRepository()
        self.cache = cache or RegistryCacheService()

    def _ensure_enabled(self) -> None:
        current_config = get_app_config()
        registry_config = current_config.get_registry_monitor_config() if current_config else {}
        manual_config = registry_config.get("manual", {}) if isinstance(registry_config, dict) else {}
        if not manual_config.get("enabled", True):
            raise RuntimeError("Manual registry entries are disabled")

    @staticmethod
    def _normalize_ip(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return str(ipaddress.ip_address(text))
        except ValueError:
            return None

    @staticmethod
    def _normalize_ip_or_cidr(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return str(ipaddress.ip_address(text))
        except ValueError:
            pass
        try:
            return str(ipaddress.ip_network(text, strict=False))
        except ValueError:
            return text

    @staticmethod
    def _normalize_dns_name(value: Optional[str]) -> Optional[str]:
        text = str(value or "").strip().rstrip(".").lower()
        if text in {"-", "—"}:
            return None
        return text or None

    @staticmethod
    def _validate_dns(ip_or_cidr: Optional[str], dns_name: Optional[str]) -> RegistryDnsValidation:
        expected_ip = RegistryManualService._normalize_ip(ip_or_cidr)
        normalized_dns = RegistryManualService._normalize_dns_name(dns_name)
        if not expected_ip or not normalized_dns:
            return RegistryDnsValidation(status="skipped", resolved_ips=[], message="DNS check skipped")
        if "*" in normalized_dns:
            return RegistryDnsValidation(status="skipped", resolved_ips=[], message=f"Wildcard DNS skipped: {normalized_dns}")

        resolved_ips: List[str] = []
        try:
            infos = socket.getaddrinfo(normalized_dns.rstrip("."), None, proto=socket.IPPROTO_TCP)
            for info in infos:
                sockaddr = info[4]
                if not sockaddr:
                    continue
                resolved_ip = RegistryManualService._normalize_ip(sockaddr[0])
                if resolved_ip and resolved_ip not in resolved_ips:
                    resolved_ips.append(resolved_ip)
        except socket.gaierror as exc:
            return RegistryDnsValidation(
                status="unresolved",
                resolved_ips=[],
                message=f"{normalized_dns}: {exc}",
            )

        if expected_ip in resolved_ips:
            return RegistryDnsValidation(status="ok", resolved_ips=resolved_ips)
        if resolved_ips:
            return RegistryDnsValidation(
                status="mismatch",
                resolved_ips=resolved_ips,
                message=f"Expected {expected_ip}, resolved {', '.join(resolved_ips)}",
            )
        return RegistryDnsValidation(status="skipped", resolved_ips=[], message="DNS check skipped")

    @classmethod
    def _coerce_dns_validation(cls, row: dict) -> RegistryDnsValidation:
        raw_validation = row.get("dns_validation")
        if isinstance(raw_validation, RegistryDnsValidation):
            return raw_validation
        if isinstance(raw_validation, dict):
            try:
                return RegistryDnsValidation(**raw_validation)
            except Exception:
                pass
        return cls._validate_dns(row.get("ip_or_cidr") or row.get("ip"), row.get("dns_name"))

    @classmethod
    def _with_dns_validation(cls, row: dict) -> dict:
        dns_validation = cls._coerce_dns_validation(row)
        return {
            **row,
            "dns_validation": dns_validation.model_dump(mode="json"),
        }

    @classmethod
    def _rows_need_dns_validation(cls, rows: List[dict]) -> bool:
        return any(not isinstance(row.get("dns_validation"), dict) for row in rows)

    @classmethod
    def _to_model(cls, row: dict) -> RegistryManualRow:
        dns_name = row.get("dns_name")
        dns_validation = cls._coerce_dns_validation(row)
        return RegistryManualRow(
            id=int(row["id"]),
            label=row["label"],
            ip=row.get("ip") or row.get("ip_or_cidr") or "",
            ip_or_cidr=row.get("ip_or_cidr") or row.get("ip") or "",
            address_scope=classify_registry_address(row.get("ip_or_cidr") or row.get("ip")) or "external",
            service_name=row.get("service_name") or "",
            dns_name=dns_name,
            dns_names=[dns_name] if dns_name else [],
            dns_validation=dns_validation,
            previous_ips=row.get("previous_ips") or [],
            ip_changed_at=row.get("ip_changed_at"),
            protocol=row.get("protocol"),
            port=row.get("port"),
            comment=row.get("comment") or "",
            owner=row.get("owner"),
            created_by=row.get("created_by"),
            tags=row.get("tags") or [],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_active=bool(row.get("is_active", True)),
        )

    @staticmethod
    def _validate_requested_scope(ip_or_cidr: Optional[str], requested_scope: Optional[str]) -> None:
        normalized_scope = str(requested_scope or "").strip().lower()
        if not normalized_scope:
            return
        actual_scope = classify_registry_address(ip_or_cidr)
        if actual_scope is None:
            raise ValueError(
                "Адрес не попадает ни в одну вкладку по текущим include/exclude правилам. "
                "Проверьте IP/CIDR или конфиг internal/external."
            )
        return

    def _ensure_unique_manual_binding(
        self,
        ip_or_cidr: Optional[str],
        dns_name: Optional[str],
        *,
        exclude_entry_id: Optional[int] = None,
    ) -> None:
        normalized_ip_or_cidr = self._normalize_ip_or_cidr(ip_or_cidr)
        normalized_dns_name = self._normalize_dns_name(dns_name)
        if not normalized_ip_or_cidr or not normalized_dns_name:
            return
        duplicate = self.repository.find_active_manual_duplicate(
            normalized_ip_or_cidr=normalized_ip_or_cidr,
            normalized_dns_name=normalized_dns_name,
            exclude_row_id=exclude_entry_id,
        )
        if duplicate:
            duplicate_service = str(duplicate.get("service_name") or duplicate.get("label") or duplicate.get("id") or "").strip()
            if duplicate_service:
                raise ValueError(
                    f'Такая связка IP и DNS уже указана у сервиса "{duplicate_service}".'
                )
            raise ValueError("Такая связка IP и DNS уже существует в ручных записях.")

    def _load_rows_from_db(self) -> List[dict]:
        rows = self.repository.list_manual_rows()
        rows = self.repository.enrich_rows_with_dns_history("manual", rows)
        return [self._with_dns_validation(row) for row in rows]

    def _refresh_cached_rows(self) -> List[dict]:
        rows = self.repository.list_manual_rows()
        self.repository.sync_dns_history("manual", rows)
        rows = self.repository.enrich_rows_with_dns_history("manual", rows)
        rows = [self._with_dns_validation(row) for row in rows]
        self.cache.save_manual_rows(rows)
        self.cache.delete_mismatch_summary()
        return rows

    def list_rows(self) -> List[RegistryManualRow]:
        cached_rows = self.cache.list_manual_rows()
        if not cached_rows:
            cached_rows = self._load_rows_from_db()
            self.cache.save_manual_rows(cached_rows)
        elif self._rows_need_dns_validation(cached_rows):
            cached_rows = self._refresh_cached_rows()
        return [self._to_model(row) for row in cached_rows]

    def create_row(
        self,
        payload: CreateManualRegistryEntryRequest,
        owner: Optional[str] = None,
    ) -> RegistryManualRow:
        self._ensure_enabled()
        self._validate_requested_scope(payload.ip_or_cidr, payload.address_scope)
        normalized_dns_name = self._normalize_dns_name(payload.dns_name)
        self._ensure_unique_manual_binding(payload.ip_or_cidr, normalized_dns_name)
        row = self.repository.create_manual_row(
            {
                "label": (payload.label or payload.service_name).strip(),
                "service_name": payload.service_name,
                "ip_or_cidr": payload.ip_or_cidr,
                "dns_name": normalized_dns_name,
                "protocol": payload.protocol,
                "port": payload.port,
                "comment": payload.comment,
                "owner": payload.owner,
                "created_by": owner,
                "tags": payload.tags,
                "is_active": payload.is_active,
            }
        )
        rows = self._refresh_cached_rows()
        created_row = next((item for item in rows if int(item.get("id")) == int(row["id"])), row)
        return self._to_model(created_row)

    def update_row(
        self,
        entry_id: int,
        payload: UpdateManualRegistryEntryRequest,
        owner: Optional[str] = None,
    ) -> RegistryManualRow:
        self._ensure_enabled()
        current_row = self.repository.get_manual_row(entry_id)
        updates = payload.model_dump(exclude_unset=True)
        if "service_name" in updates and "label" not in updates:
            service_name = str(updates.get("service_name") or "").strip()
            if service_name:
                updates["label"] = service_name
        target_ip_or_cidr = updates.get("ip_or_cidr", current_row.get("ip_or_cidr"))
        target_dns_name = updates.get("dns_name", current_row.get("dns_name"))
        requested_scope = updates.get("address_scope")
        self._validate_requested_scope(target_ip_or_cidr, requested_scope)
        normalized_dns_name = self._normalize_dns_name(target_dns_name)
        self._ensure_unique_manual_binding(target_ip_or_cidr, normalized_dns_name, exclude_entry_id=entry_id)
        if "dns_name" in updates:
            updates["dns_name"] = normalized_dns_name
        updates.pop("address_scope", None)
        row = self.repository.update_manual_row(entry_id, updates)
        rows = self._refresh_cached_rows()
        updated_row = next((item for item in rows if int(item.get("id")) == int(row["id"])), row)
        return self._to_model(updated_row)

    def delete_row(self, entry_id: int) -> bool:
        self._ensure_enabled()
        deleted = self.repository.delete_manual_row(entry_id)
        self._refresh_cached_rows()
        return deleted


registry_manual_service = RegistryManualService()
