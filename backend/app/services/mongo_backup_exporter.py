"""
Client for the read-only MongoDB backup exporter.
"""
import json
import logging
import time
from typing import Any, Dict, Iterable, Optional
from urllib.request import Request
from app.services.http_client import open_http as urlopen

from app.config import get_app_config, settings
from app.services.cache import CacheService

logger = logging.getLogger(__name__)
BACKUP_EXPORTER_CACHE_KEY = "mongo_backups:exporter_snapshot"


class MongoBackupExporterClient:
    """Fetch last completed MongoDB backup facts from mongo-backup-exporter."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_s: float = 3.0,
        cache_ttl_s: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.cache_ttl_s = max(0, cache_ttl_s)
        self._cache_payload: Optional[Dict[str, Any]] = None
        self._cache_expires_at = 0.0
        self._redis_cache = CacheService()

    @classmethod
    def from_config(cls) -> Optional["MongoBackupExporterClient"]:
        current_config = get_app_config()
        if not current_config:
            return None

        config = current_config.get_mongo_backups_config()
        enabled_raw = config.get("enabled", False)
        enabled = (
            str(enabled_raw).strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(enabled_raw, str)
            else bool(enabled_raw)
        )
        if not config or not enabled:
            return None

        base_url = str(config.get("baseUrl") or config.get("url") or "").strip()
        token = str(settings.mongo_backups_token or "").strip()
        if not base_url or not token:
            return None

        try:
            timeout_s = float(config.get("timeoutS", 3.0) or 3.0)
        except (TypeError, ValueError):
            timeout_s = 3.0

        try:
            cache_ttl_s = int(config.get("cacheTtlS", 30) or 30)
        except (TypeError, ValueError):
            cache_ttl_s = 30

        return cls(
            base_url=base_url,
            token=token,
            timeout_s=timeout_s,
            cache_ttl_s=cache_ttl_s,
        )

    def _url(self) -> str:
        if self.base_url.endswith("/api/v1/mongo-backups"):
            return self.base_url
        if self.base_url.endswith("/api/v1"):
            return f"{self.base_url}/mongo-backups"
        return f"{self.base_url}/api/v1/mongo-backups"

    def _fetch_payload(self) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        if self._cache_payload is not None and now < self._cache_expires_at:
            return self._cache_payload

        if self.cache_ttl_s > 0:
            cached_payload = self._redis_cache.get_json(BACKUP_EXPORTER_CACHE_KEY)
            if isinstance(cached_payload, dict) and cached_payload.get("status") == "ok":
                self._cache_payload = cached_payload
                self._cache_expires_at = now + self.cache_ttl_s
                return cached_payload

        request = Request(
            self._url(),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            logger.debug("Could not fetch MongoDB backup exporter data: %s", exc)
            return None

        if not isinstance(payload, dict) or payload.get("status") != "ok":
            logger.debug("MongoDB backup exporter returned non-ok payload: %s", payload)
            return None

        self._cache_payload = payload
        self._cache_expires_at = now + self.cache_ttl_s
        if self.cache_ttl_s > 0:
            self._redis_cache.set_json(BACKUP_EXPORTER_CACHE_KEY, payload, self.cache_ttl_s)
        return payload

    def get_cluster_backups(
        self,
        cluster_name: str,
        databases: Iterable[str],
    ) -> Dict[str, Dict[str, Any]]:
        wanted_databases = {str(database) for database in databases if str(database)}
        if not wanted_databases:
            return {}

        payload = self._fetch_payload()
        if not payload:
            return {}

        clusters = payload.get("clusters")
        if not isinstance(clusters, list):
            return {}

        for cluster in clusters:
            if not isinstance(cluster, dict) or cluster.get("cluster") != cluster_name:
                continue
            result: Dict[str, Dict[str, Any]] = {}
            for database in cluster.get("databases", []):
                if not isinstance(database, dict):
                    continue
                database_name = str(database.get("database") or "")
                if database_name in wanted_databases:
                    result[database_name] = database
            return result
        return {}


_client: Optional[MongoBackupExporterClient] = None


def get_mongo_backup_exporter_client() -> Optional[MongoBackupExporterClient]:
    global _client
    if _client is None:
        _client = MongoBackupExporterClient.from_config()
    return _client
