from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from redis.exceptions import RedisError

from app.config import settings
from app.services.cache import CacheService

REGISTRY_CACHE_KEY = "registry:target:registry"
REGISTRY_MANUAL_ROWS_KEY = "registry:manual:rows"
REGISTRY_MISMATCH_SUMMARY_KEY = "registry:mismatch:summary"
logger = logging.getLogger(__name__)


class RegistryCacheService:
    def __init__(self) -> None:
        self.cache = CacheService(
            db=settings.redis_redis_db if settings.redis_redis_db is not None else settings.redis_db
        )

    def set_state(self, payload: Dict[str, Any]) -> None:
        summary = dict(payload)
        if summary.get("last_update") is None:
            summary["last_update"] = datetime.now(timezone.utc).isoformat()
        try:
            self.cache.redis_client.setex(REGISTRY_CACHE_KEY, 86400, json.dumps(summary, default=str))
        except RedisError as exc:
            logger.warning("Redis write failed for registry state: %s", exc)

    def get_summary(self) -> Optional[Dict[str, Any]]:
        return self.get_state()

    def get_state(self) -> Optional[Dict[str, Any]]:
        try:
            raw = self.cache.redis_client.get(REGISTRY_CACHE_KEY)
        except RedisError as exc:
            logger.warning("Redis read failed for registry state: %s", exc)
            return None
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.debug("Invalid registry state cache payload: %s", exc)
            return None
        return parsed if isinstance(parsed, dict) else None

    def set_dashboard_payload(self, payload: Dict[str, Any]) -> None:
        try:
            self.cache.redis_client.set(REGISTRY_CACHE_KEY, json.dumps(payload, default=str))
        except RedisError as exc:
            logger.warning("Redis write failed for registry dashboard payload: %s", exc)

    def get_dashboard_payload(self) -> Optional[Dict[str, Any]]:
        return self.get_state()

    def list_manual_rows(self) -> list[Dict[str, Any]]:
        try:
            raw = self.cache.redis_client.get(REGISTRY_MANUAL_ROWS_KEY)
        except RedisError as exc:
            logger.warning("Redis read failed for registry manual rows: %s", exc)
            return []
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError) as exc:
            logger.debug("Invalid registry manual rows cache payload: %s", exc)
            return []

    def save_manual_rows(self, rows: list[Dict[str, Any]]) -> None:
        try:
            self.cache.redis_client.set(REGISTRY_MANUAL_ROWS_KEY, json.dumps(rows, default=str))
        except RedisError as exc:
            logger.warning("Redis write failed for registry manual rows: %s", exc)

    def get_mismatch_summary(self) -> Optional[Dict[str, Any]]:
        try:
            raw = self.cache.redis_client.get(REGISTRY_MISMATCH_SUMMARY_KEY)
        except RedisError as exc:
            logger.warning("Redis read failed for registry mismatch summary: %s", exc)
            return None
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.debug("Invalid registry mismatch summary cache payload: %s", exc)
            return None
        return parsed if isinstance(parsed, dict) else None

    def set_mismatch_summary(self, payload: Dict[str, Any], ttl_s: int = 60) -> None:
        try:
            self.cache.redis_client.setex(
                REGISTRY_MISMATCH_SUMMARY_KEY,
                max(1, int(ttl_s)),
                json.dumps(payload, default=str),
            )
        except RedisError as exc:
            logger.warning("Redis write failed for registry mismatch summary: %s", exc)

    def delete_mismatch_summary(self) -> None:
        try:
            self.cache.redis_client.delete(REGISTRY_MISMATCH_SUMMARY_KEY)
        except RedisError as exc:
            logger.warning("Redis delete failed for registry mismatch summary: %s", exc)


registry_cache_service = RegistryCacheService()
