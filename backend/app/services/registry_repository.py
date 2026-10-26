"""
PostgreSQL repository for Registry monitor durable data.
Uses raw SQL plus idempotent schema creation, without Alembic/SQLAlchemy.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg
from psycopg.rows import dict_row

from app.config import settings

logger = logging.getLogger(__name__)
DISPLAY_DNS_SUFFIX_RE = re.compile(r"\s+\(\+\d+\)$")


class RegistryDatabaseUnavailable(RuntimeError):
    """Raised when PostgreSQL is not configured or unavailable."""


class RegistryRepository:
    """Raw SQL access layer for registry tables."""

    _schema_ready = False
    _schema_lock = threading.Lock()

    def _connection_kwargs(self) -> Dict[str, Any]:
        if not settings.postgres_host:
            raise RegistryDatabaseUnavailable("POSTGRES_HOST is not configured")
        if not settings.postgres_db:
            raise RegistryDatabaseUnavailable("POSTGRES_DB is not configured")
        if not settings.postgres_user:
            raise RegistryDatabaseUnavailable("POSTGRES_USER is not configured")
        return {
            "host": settings.postgres_host,
            "port": settings.postgres_port or 5432,
            "dbname": settings.postgres_db,
            "user": settings.postgres_user,
            "password": settings.postgres_password or "",
            "connect_timeout": 5,
        }

    @contextmanager
    def _connect(self):
        conn = psycopg.connect(**self._connection_kwargs(), row_factory=dict_row)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, default=str, ensure_ascii=False)

    @staticmethod
    def _json_loads(value: Optional[str], default: Any) -> Any:
        if not value:
            return default
        try:
            parsed = json.loads(value)
            return parsed
        except Exception:
            return default

    def ensure_schema(self) -> None:
        """Create registry tables/indexes if they do not exist."""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            ddl_statements = [
                """
                CREATE TABLE IF NOT EXISTS registry_sync_runs (
                    id BIGSERIAL PRIMARY KEY,
                    repo_name TEXT NOT NULL,
                    repo_url TEXT NOT NULL,
                    repo_ref TEXT NOT NULL,
                    repo_commit TEXT,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMPTZ,
                    details TEXT NOT NULL DEFAULT ''
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS registry_source_objects (
                    id BIGSERIAL PRIMARY KEY,
                    run_id BIGINT NOT NULL REFERENCES registry_sync_runs(id) ON DELETE CASCADE,
                    repo_name TEXT NOT NULL,
                    repo_url TEXT NOT NULL,
                    repo_ref TEXT NOT NULL,
                    repo_commit TEXT,
                    file_path TEXT NOT NULL,
                    object_key TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    extractor_type TEXT NOT NULL,
                    environment TEXT,
                    component TEXT,
                    service_name TEXT,
                    namespace TEXT,
                    source_line_start INTEGER,
                    source_line_end INTEGER,
                    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS registry_object_attributes (
                    id BIGSERIAL PRIMARY KEY,
                    source_object_id BIGINT NOT NULL REFERENCES registry_source_objects(id) ON DELETE CASCADE,
                    attr_name TEXT NOT NULL,
                    attr_type TEXT NOT NULL,
                    attr_value TEXT NOT NULL,
                    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                    line_no INTEGER,
                    position INTEGER
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS manual_registry_entries (
                    id BIGSERIAL PRIMARY KEY,
                    label TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    ip_or_cidr TEXT NOT NULL,
                    dns_name TEXT,
                    protocol TEXT,
                    port INTEGER,
                    comment TEXT NOT NULL DEFAULT '',
                    owner TEXT,
                    created_by TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS registry_linked_rows (
                    id BIGSERIAL PRIMARY KEY,
                    source_object_ids_json TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    service_name TEXT,
                    dns_name TEXT,
                    dns_names_json TEXT NOT NULL DEFAULT '[]',
                    protocol TEXT,
                    port INTEGER,
                    origin TEXT NOT NULL,
                    environment TEXT,
                    component TEXT,
                    source_references_json TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS registry_monitor_state (
                    name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_update TIMESTAMPTZ,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS registry_dns_history (
                    id BIGSERIAL PRIMARY KEY,
                    origin TEXT NOT NULL,
                    entry_key TEXT NOT NULL,
                    dns_name TEXT NOT NULL,
                    service_name TEXT,
                    ip TEXT NOT NULL,
                    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    valid_to TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
                "CREATE INDEX IF NOT EXISTS idx_registry_sync_runs_repo_name_started_at ON registry_sync_runs (repo_name, started_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_registry_source_objects_repo_name ON registry_source_objects (repo_name)",
                "CREATE INDEX IF NOT EXISTS idx_registry_source_objects_run_id ON registry_source_objects (run_id)",
                "CREATE INDEX IF NOT EXISTS idx_registry_object_attributes_source_object_id ON registry_object_attributes (source_object_id)",
                "CREATE INDEX IF NOT EXISTS idx_registry_object_attributes_attr_type ON registry_object_attributes (attr_type)",
                "CREATE INDEX IF NOT EXISTS idx_registry_manual_entries_active ON manual_registry_entries (is_active, updated_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_registry_manual_entries_ip_dns ON manual_registry_entries (ip_or_cidr, dns_name)",
                "CREATE INDEX IF NOT EXISTS idx_registry_linked_rows_ip ON registry_linked_rows (ip)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_registry_dns_history_active_unique ON registry_dns_history (origin, entry_key, dns_name, ip) WHERE valid_to IS NULL",
                "CREATE INDEX IF NOT EXISTS idx_registry_dns_history_lookup ON registry_dns_history (origin, entry_key, dns_name, valid_from DESC)",
            ]
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for stmt in ddl_statements:
                        cur.execute(stmt)
                    cur.execute(
                        """
                        ALTER TABLE registry_linked_rows
                        ADD COLUMN IF NOT EXISTS dns_names_json TEXT NOT NULL DEFAULT '[]'
                        """
                    )
                    cur.execute(
                        """
                        ALTER TABLE manual_registry_entries
                        ADD COLUMN IF NOT EXISTS created_by TEXT
                        """
                    )
            self._schema_ready = True
            logger.info("Registry schema ensured")

    def record_sync_run(
        self,
        *,
        repo_name: str,
        repo_url: str,
        repo_ref: str,
        repo_commit: Optional[str],
        status: str,
        started_at: datetime,
        finished_at: Optional[datetime] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Persist a sync run row and return its id."""
        self.ensure_schema()
        details_text = self._json_dumps(details or {})
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO registry_sync_runs (
                        repo_name, repo_url, repo_ref, repo_commit, status, started_at, finished_at, details
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        repo_name,
                        repo_url,
                        repo_ref,
                        repo_commit,
                        status,
                        started_at,
                        finished_at,
                        details_text,
                    ),
                )
                row = cur.fetchone() or {}
                return int(row["id"])

    def replace_source_objects(
        self,
        *,
        repo_name: str,
        source_rows: List[Dict[str, Any]],
        run_id: int,
    ) -> List[int]:
        """Replace all source objects for a repo and return inserted ids."""
        self.ensure_schema()
        inserted_ids: List[int] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM registry_object_attributes
                    WHERE source_object_id IN (
                        SELECT id FROM registry_source_objects WHERE repo_name = %s
                    )
                    """,
                    (repo_name,),
                )
                cur.execute("DELETE FROM registry_source_objects WHERE repo_name = %s", (repo_name,))
                for source_row in source_rows:
                    cur.execute(
                        """
                        INSERT INTO registry_source_objects (
                            run_id, repo_name, repo_url, repo_ref, repo_commit,
                            file_path, object_key, object_type, extractor_type,
                            environment, component, service_name, namespace,
                            source_line_start, source_line_end, collected_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s
                        )
                        RETURNING id
                        """,
                        (
                            run_id,
                            repo_name,
                            source_row.get("repo_url") or "",
                            source_row.get("repo_ref") or "",
                            source_row.get("repo_commit"),
                            source_row.get("file_path") or "",
                            source_row.get("object_key") or "",
                            source_row.get("object_type") or "unknown",
                            source_row.get("extractor_type") or "unknown",
                            source_row.get("environment"),
                            source_row.get("component"),
                            source_row.get("service_name"),
                            source_row.get("namespace"),
                            source_row.get("source_line_start"),
                            source_row.get("source_line_end"),
                            source_row.get("collected_at") or datetime.now(timezone.utc),
                        ),
                    )
                    row = cur.fetchone() or {}
                    source_object_id = int(row["id"])
                    inserted_ids.append(source_object_id)
                    attrs = source_row.get("attributes") or []
                    for attr in attrs:
                        cur.execute(
                            """
                            INSERT INTO registry_object_attributes (
                                source_object_id, attr_name, attr_type, attr_value,
                                is_primary, line_no, position
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                source_object_id,
                                attr.get("attr_name") or "",
                                attr.get("attr_type") or "raw",
                                str(attr.get("attr_value") or ""),
                                bool(attr.get("is_primary", False)),
                                attr.get("line_no"),
                                attr.get("position"),
                            ),
                        )
        return inserted_ids

    def list_source_objects(self, repo_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return source objects with joined attributes."""
        self.ensure_schema()
        params: Tuple[Any, ...] = ()
        repo_clause = ""
        if repo_name:
            repo_clause = "WHERE so.repo_name = %s"
            params = (repo_name,)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        so.id,
                        so.run_id,
                        so.repo_name,
                        so.repo_url,
                        so.repo_ref,
                        so.repo_commit,
                        so.file_path,
                        so.object_key,
                        so.object_type,
                        so.extractor_type,
                        so.environment,
                        so.component,
                        so.service_name,
                        so.namespace,
                        so.source_line_start,
                        so.source_line_end,
                        so.collected_at,
                        COALESCE(
                            json_agg(
                                json_build_object(
                                    'attr_name', a.attr_name,
                                    'attr_type', a.attr_type,
                                    'attr_value', a.attr_value,
                                    'is_primary', a.is_primary,
                                    'line_no', a.line_no,
                                    'position', a.position
                                )
                                ORDER BY a.id
                            ) FILTER (WHERE a.id IS NOT NULL),
                            '[]'
                        ) AS attributes
                    FROM registry_source_objects so
                    LEFT JOIN registry_object_attributes a ON a.source_object_id = so.id
                    {repo_clause}
                    GROUP BY so.id
                    ORDER BY so.repo_name, so.file_path, so.id
                    """,
                    params,
                )
                rows = cur.fetchall() or []
                result: List[Dict[str, Any]] = []
                for row in rows:
                    attributes = row.get("attributes") or []
                    if isinstance(attributes, str):
                        attributes = self._json_loads(attributes, [])
                    if not isinstance(attributes, list):
                        attributes = []
                    result.append(
                        {
                            **row,
                            "attributes": attributes,
                        }
                    )
                return result

    def delete_linked_rows(self) -> None:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM registry_linked_rows")

    def insert_linked_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.ensure_schema()
        if not rows:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO registry_linked_rows (
                            source_object_ids_json, ip, service_name, dns_name, dns_names_json, protocol, port,
                            origin, environment, component, source_references_json, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            self._json_dumps(row.get("source_object_ids") or []),
                            row.get("ip") or "",
                            row.get("service_name"),
                            row.get("dns_name"),
                            self._json_dumps(row.get("dns_names") or ([] if not row.get("dns_name") else [row.get("dns_name")])),
                            row.get("protocol"),
                            row.get("port"),
                            row.get("origin") or "auto_gitlab",
                            row.get("environment"),
                            row.get("component"),
                            self._json_dumps(row.get("source_references") or []),
                            row.get("created_at") or datetime.now(timezone.utc),
                            row.get("updated_at") or datetime.now(timezone.utc),
                        ),
                    )

    def list_linked_rows(self) -> List[Dict[str, Any]]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        source_object_ids_json,
                        ip,
                        service_name,
                        dns_name,
                        dns_names_json,
                        protocol,
                        port,
                        origin,
                        environment,
                        component,
                        source_references_json,
                        created_at,
                        updated_at
                    FROM registry_linked_rows
                    ORDER BY ip, service_name NULLS LAST, dns_name NULLS LAST, id
                    """
                )
                rows = cur.fetchall() or []
        result: List[Dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    **row,
                    "source_object_ids": self._json_loads(row.get("source_object_ids_json"), []),
                    "source_references": self._json_loads(row.get("source_references_json"), []),
                    "dns_names": self._json_loads(row.get("dns_names_json"), []),
                }
            )
        return result

    def list_manual_rows(self) -> List[Dict[str, Any]]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        label,
                        service_name,
                        ip_or_cidr,
                        dns_name,
                        protocol,
                        port,
                        comment,
                        owner,
                        created_by,
                        tags_json,
                        created_at,
                        updated_at,
                        is_active
                    FROM manual_registry_entries
                    ORDER BY updated_at DESC, id DESC
                    """
                )
                rows = cur.fetchall() or []
        result: List[Dict[str, Any]] = []
        for row in rows:
            tags = self._json_loads(row.get("tags_json"), [])
            if not isinstance(tags, list):
                tags = []
            result.append(
                {
                    **row,
                    "tags": tags,
                    "ip": row.get("ip_or_cidr"),
                }
            )
        return result

    @staticmethod
    def _normalize_display_dns_name(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        return DISPLAY_DNS_SUFFIX_RE.sub("", text)

    @staticmethod
    def _history_dns_names(row: Dict[str, Any]) -> List[str]:
        dns_names = row.get("dns_names")
        if isinstance(dns_names, list):
            values = dns_names
        elif row.get("dns_name"):
            values = [row.get("dns_name")]
        else:
            values = []
        normalized: List[str] = []
        for value in values:
            text = RegistryRepository._normalize_display_dns_name(value)
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _history_normalize_ip(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            import ipaddress
            return str(ipaddress.ip_address(text))
        except Exception:
            return None

    @staticmethod
    def _history_entry_key(origin: str, row: Dict[str, Any]) -> str:
        if origin == "manual":
            if row.get("id") is not None:
                return f"manual:{row.get('id')}"
        service_name = str(row.get("service_name") or "").strip()
        environment = str(row.get("environment") or "").strip()
        component = str(row.get("component") or "").strip()
        source_refs = row.get("source_references") or []
        source_key = ""
        if isinstance(source_refs, list) and source_refs:
            first_ref = source_refs[0] or {}
            source_key = str(first_ref.get("object_key") or first_ref.get("file_path") or "").strip()
        return "|".join([origin, service_name, environment, component, source_key])

    def sync_dns_history(self, origin: str, rows: List[Dict[str, Any]], observed_at: Optional[datetime] = None) -> None:
        self.ensure_schema()
        now = observed_at or datetime.now(timezone.utc)
        current_records: List[Tuple[str, str, str, str, Optional[str]]] = []
        current_pairs: set[Tuple[str, str]] = set()
        for row in rows:
            entry_key = self._history_entry_key(origin, row)
            ip = self._history_normalize_ip(row.get("ip") or row.get("ip_or_cidr"))
            if not entry_key or not ip:
                continue
            service_name = row.get("service_name")
            for dns_name in self._history_dns_names(row):
                current_records.append((entry_key, dns_name, ip, origin, service_name))
                current_pairs.add((entry_key, dns_name))

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, entry_key, dns_name, ip
                    FROM registry_dns_history
                    WHERE origin = %s AND valid_to IS NULL
                    """,
                    (origin,),
                )
                active_rows = cur.fetchall() or []
                active_by_triplet = {
                    (str(row.get("entry_key") or ""), str(row.get("dns_name") or ""), str(row.get("ip") or "")): int(row["id"])
                    for row in active_rows
                }
                for row in active_rows:
                    pair = (str(row.get("entry_key") or ""), str(row.get("dns_name") or ""))
                    if pair not in current_pairs:
                        cur.execute(
                            """
                            UPDATE registry_dns_history
                            SET valid_to = %s, last_seen_at = %s, updated_at = %s
                            WHERE id = %s
                            """,
                            (now, now, now, int(row["id"])),
                        )

                for entry_key, dns_name, ip, row_origin, service_name in current_records:
                    triplet = (entry_key, dns_name, ip)
                    active_id = active_by_triplet.get(triplet)
                    if active_id is not None:
                        cur.execute(
                            """
                            UPDATE registry_dns_history
                            SET last_seen_at = %s, updated_at = %s, service_name = %s
                            WHERE id = %s
                            """,
                            (now, now, service_name, active_id),
                        )
                        continue
                    cur.execute(
                        """
                        UPDATE registry_dns_history
                        SET valid_to = %s, last_seen_at = %s, updated_at = %s
                        WHERE origin = %s AND entry_key = %s AND dns_name = %s AND valid_to IS NULL
                        """,
                        (now, now, now, row_origin, entry_key, dns_name),
                    )
                    cur.execute(
                        """
                        INSERT INTO registry_dns_history (
                            origin, entry_key, dns_name, service_name, ip,
                            first_seen_at, last_seen_at, valid_from, valid_to, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)
                        """,
                        (row_origin, entry_key, dns_name, service_name, ip, now, now, now, now, now),
                    )

    def enrich_rows_with_dns_history(self, origin: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self.ensure_schema()
        history_keys: List[Tuple[str, str]] = []
        row_keys: List[Tuple[str, List[str], Optional[str]]] = []
        for row in rows:
            entry_key = self._history_entry_key(origin, row)
            dns_names = self._history_dns_names(row)
            current_ip = self._history_normalize_ip(row.get("ip") or row.get("ip_or_cidr"))
            row_keys.append((entry_key, dns_names, current_ip))
            for dns_name in dns_names:
                key = (entry_key, dns_name)
                if key not in history_keys:
                    history_keys.append(key)
        if not history_keys:
            return rows

        history_rows: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                for entry_key, dns_name in history_keys:
                    cur.execute(
                        """
                        SELECT ip, valid_from, valid_to
                        FROM registry_dns_history
                        WHERE origin = %s AND entry_key = %s AND dns_name = %s
                        ORDER BY valid_from DESC, id DESC
                        """,
                        (origin, entry_key, dns_name),
                    )
                    history_rows[(entry_key, dns_name)] = cur.fetchall() or []

        enriched: List[Dict[str, Any]] = []
        for row, (entry_key, dns_names, current_ip) in zip(rows, row_keys):
            previous_ips: List[str] = []
            ip_changed_at: Optional[datetime] = None
            for dns_name in dns_names:
                for history_row in history_rows.get((entry_key, dns_name), []):
                    ip = str(history_row.get("ip") or "").strip()
                    if not ip:
                        continue
                    if current_ip and ip == current_ip:
                        valid_from = history_row.get("valid_from")
                        if valid_from and ip_changed_at is None:
                            has_previous = any(
                                str(candidate.get("ip") or "").strip() != current_ip
                                for candidate in history_rows.get((entry_key, dns_name), [])
                            )
                            if has_previous:
                                ip_changed_at = valid_from
                        continue
                    if ip not in previous_ips:
                        previous_ips.append(ip)
                    valid_to = history_row.get("valid_to") or history_row.get("valid_from")
                    if valid_to and (ip_changed_at is None or valid_to > ip_changed_at):
                        ip_changed_at = valid_to
            enriched.append(
                {
                    **row,
                    "previous_ips": previous_ips,
                    "ip_changed_at": ip_changed_at,
                }
            )
        return enriched

    def create_manual_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_schema()
        now = datetime.now(timezone.utc)
        tags_json = self._json_dumps(row.get("tags") or [])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO manual_registry_entries (
                        label, service_name, ip_or_cidr, dns_name, protocol, port,
                        comment, owner, created_by, tags_json, created_at, updated_at, is_active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        row.get("label") or "",
                        row.get("service_name") or "",
                        row.get("ip_or_cidr") or "",
                        row.get("dns_name"),
                        row.get("protocol"),
                        row.get("port"),
                        row.get("comment") or "",
                        row.get("owner"),
                        row.get("created_by"),
                        tags_json,
                        now,
                        now,
                        bool(row.get("is_active", True)),
                    ),
                )
                created = cur.fetchone() or {}
                row_id = int(created["id"])
        return self.get_manual_row(row_id)

    def get_manual_row(self, row_id: int) -> Dict[str, Any]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        label,
                        service_name,
                        ip_or_cidr,
                        dns_name,
                        protocol,
                        port,
                        comment,
                        owner,
                        created_by,
                        tags_json,
                        created_at,
                        updated_at,
                        is_active
                    FROM manual_registry_entries
                    WHERE id = %s
                    """,
                    (row_id,),
                )
                row = cur.fetchone()
        if not row:
            raise KeyError(f"Manual registry row {row_id} not found")
        tags = self._json_loads(row.get("tags_json"), [])
        if not isinstance(tags, list):
            tags = []
        return {
            **row,
            "tags": tags,
            "ip": row.get("ip_or_cidr"),
        }

    def find_active_manual_duplicate(
        self,
        *,
        normalized_ip_or_cidr: str,
        normalized_dns_name: str,
        exclude_row_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                if exclude_row_id is None:
                    cur.execute(
                        """
                        SELECT
                            id,
                            label,
                            service_name,
                            ip_or_cidr,
                            dns_name,
                            protocol,
                            port,
                            comment,
                            owner,
                            created_by,
                            tags_json,
                            created_at,
                            updated_at,
                            is_active
                        FROM manual_registry_entries
                        WHERE is_active = TRUE
                        ORDER BY updated_at DESC, id DESC
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT
                            id,
                            label,
                            service_name,
                            ip_or_cidr,
                            dns_name,
                            protocol,
                            port,
                            comment,
                            owner,
                            created_by,
                            tags_json,
                            created_at,
                            updated_at,
                            is_active
                        FROM manual_registry_entries
                        WHERE is_active = TRUE
                          AND id <> %s
                        ORDER BY updated_at DESC, id DESC
                        """,
                        (exclude_row_id,),
                    )
                rows = cur.fetchall() or []
        for row in rows:
            current_ip_or_cidr = str(row.get("ip_or_cidr") or "").strip()
            current_dns_name = str(row.get("dns_name") or "").strip().rstrip(".").lower()
            if current_ip_or_cidr == normalized_ip_or_cidr and current_dns_name == normalized_dns_name:
                tags = self._json_loads(row.get("tags_json"), [])
                if not isinstance(tags, list):
                    tags = []
                return {
                    **row,
                    "tags": tags,
                    "ip": row.get("ip_or_cidr"),
                }
        return None

    def update_manual_row(self, row_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_schema()
        fields = []
        values: List[Any] = []
        mapping = {
            "label": "label",
            "service_name": "service_name",
            "ip_or_cidr": "ip_or_cidr",
            "dns_name": "dns_name",
            "protocol": "protocol",
            "port": "port",
            "comment": "comment",
            "owner": "owner",
            "is_active": "is_active",
        }
        for source_key, column_name in mapping.items():
            if source_key in updates and updates[source_key] is not None:
                fields.append(f"{column_name} = %s")
                values.append(updates[source_key])
        if "tags" in updates and updates["tags"] is not None:
            fields.append("tags_json = %s")
            values.append(self._json_dumps(updates["tags"]))
        if not fields:
            return self.get_manual_row(row_id)
        fields.append("updated_at = %s")
        values.append(datetime.now(timezone.utc))
        values.append(row_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE manual_registry_entries
                    SET {", ".join(fields)}
                    WHERE id = %s
                    """,
                    tuple(values),
                )
        return self.get_manual_row(row_id)

    def delete_manual_row(self, row_id: int) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM manual_registry_entries WHERE id = %s", (row_id,))
                if cur.rowcount == 0:
                    raise KeyError(f"Manual registry row {row_id} not found")
        return True

    def get_latest_run_summaries(self) -> List[Dict[str, Any]]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (repo_name)
                        repo_name,
                        repo_ref,
                        repo_commit,
                        status,
                        started_at,
                        finished_at,
                        details
                    FROM registry_sync_runs
                    ORDER BY repo_name, started_at DESC, id DESC
                    """
                )
                rows = cur.fetchall() or []
        result: List[Dict[str, Any]] = []
        for row in rows:
            result.append({**row})
        return result

    def get_summary(self, refresh_interval_s: int = 1200) -> Dict[str, Any]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS count FROM registry_source_objects")
                source_objects = int((cur.fetchone() or {}).get("count", 0))
                cur.execute("SELECT COUNT(*) AS count FROM registry_object_attributes")
                attributes = int((cur.fetchone() or {}).get("count", 0))
                cur.execute("SELECT COUNT(*) AS count FROM registry_linked_rows")
                linked_rows = int((cur.fetchone() or {}).get("count", 0))
                cur.execute("SELECT COUNT(*) AS count FROM manual_registry_entries")
                manual_rows = int((cur.fetchone() or {}).get("count", 0))
                cur.execute(
                    """
                    SELECT status, started_at, finished_at
                    FROM registry_sync_runs
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                    """
                )
                latest_run = cur.fetchone() or {}
                cur.execute(
                    """
                    SELECT MAX(finished_at) AS last_successful_sync_at
                    FROM registry_sync_runs
                    WHERE status = 'success'
                    """
                )
                successful = cur.fetchone() or {}
        repo_summaries = self.get_latest_run_summaries()
        summary = {
            "auto_rows_count": linked_rows,
            "manual_rows_count": manual_rows,
            "linked_rows_count": linked_rows,
            "total_source_objects": source_objects,
            "total_attributes": attributes,
            "total_linked_rows": linked_rows,
            "total_manual_rows": manual_rows,
            "last_sync_status": latest_run.get("status") or "unknown",
            "last_sync_at": latest_run.get("finished_at") or latest_run.get("started_at"),
            "last_successful_sync_at": successful.get("last_successful_sync_at"),
            "refresh_interval_s": refresh_interval_s,
            "repos": [
                {
                    "repo_name": row.get("repo_name"),
                    "repo_ref": row.get("repo_ref"),
                    "repo_commit": row.get("repo_commit"),
                    "status": row.get("status") or "unknown",
                    "last_synced_at": row.get("finished_at") or row.get("started_at"),
                    "error": (self._json_loads(row.get("details"), {}) or {}).get("error"),
                }
                for row in repo_summaries
            ],
        }
        return summary

    def upsert_monitor_state(self, *, status: str, last_update: Optional[datetime], summary: Dict[str, Any]) -> None:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO registry_monitor_state (name, status, last_update, summary_json, updated_at)
                    VALUES ('registry', %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE
                    SET status = EXCLUDED.status,
                        last_update = EXCLUDED.last_update,
                        summary_json = EXCLUDED.summary_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        status,
                        last_update,
                        self._json_dumps(summary),
                        datetime.now(timezone.utc),
                    ),
                )

    def get_monitor_state(self) -> Optional[Dict[str, Any]]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name, status, last_update, summary_json, updated_at
                    FROM registry_monitor_state
                    WHERE name = 'registry'
                    """
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            **row,
            "summary": self._json_loads(row.get("summary_json"), {}),
        }

    def list_auto_rows(self) -> List[Dict[str, Any]]:
        """Return final linked auto rows."""
        return self.list_linked_rows()

    def create_manual_entry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.create_manual_row(payload)

    def update_manual_entry(self, entry_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.update_manual_row(entry_id, payload)

    def delete_manual_entry(self, entry_id: int) -> bool:
        try:
            self.delete_manual_row(entry_id)
            return True
        except KeyError:
            return False


registry_repository = RegistryRepository()
