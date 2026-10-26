"""
Operation tracker with Redis-backed storage and in-memory fallback.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationsTracker:
    """Track long-running UI operations with timeline and TTL.

    Keeps the last N operations with a long TTL; older operations get a short TTL.
    """

    def __init__(
        self,
        ttl_seconds: int = 1800,
        redis_client: Optional[Any] = None,
        redis_prefix: str = "haproxy:operations",
        ttl_seconds_long: Optional[int] = None,
        ttl_seconds_short: int = 300,
        keep_last_n: int = 3,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._ttl_seconds_long = ttl_seconds_long or ttl_seconds
        self._ttl_seconds_short = ttl_seconds_short
        self._keep_last_n = max(1, keep_last_n)
        self._redis_client = redis_client
        self._redis_prefix = redis_prefix.rstrip(":")
        self._lock = threading.Lock()
        self._items: Dict[str, Dict[str, Any]] = {}

    def _operation_key(self, operation_id: str) -> str:
        return f"{self._redis_prefix}:item:{operation_id}"

    def _target_index_key(self, target_name: str) -> str:
        return f"{self._redis_prefix}:target:{target_name}:index"

    def _purge_expired_locked(self) -> None:
        now_ts = time.time()
        expired_ids = [
            operation_id
            for operation_id, item in self._items.items()
            if (now_ts - float(item.get("updated_at_ts", now_ts))) > self._ttl_seconds
        ]
        for operation_id in expired_ids:
            self._items.pop(operation_id, None)

    def _save_redis(self, item: Dict[str, Any]) -> None:
        if not self._redis_client:
            return
        operation_id = str(item.get("id") or "")
        target_name = str(item.get("target_name") or "")
        updated_at_ts = float(item.get("updated_at_ts") or time.time())
        if not operation_id or not target_name:
            return

        payload = json.dumps(item, default=str)
        item_key = self._operation_key(operation_id)
        index_key = self._target_index_key(target_name)
        # Save with long TTL; cleanup later decides which ones to keep long
        pipe = self._redis_client.pipeline()
        pipe.setex(item_key, self._ttl_seconds_long, payload)
        pipe.zadd(index_key, {operation_id: updated_at_ts})
        pipe.execute()

    def _cleanup_old_operations(self, target_name: str) -> None:
        """Set short TTL on operations beyond the last N recent ones.

        Last N operations are kept with long/persistent TTL; older ones get short TTL.
        """
        if not self._redis_client:
            return
        index_key = self._target_index_key(target_name)
        # Get all operation IDs sorted by recency (newest first)
        all_ids = self._redis_client.zrevrange(index_key, 0, -1) or []
        if not all_ids:
            return

        # Determine which operations to keep long vs short
        keep_long_ids = set(str(op_id) for op_id in all_ids[: self._keep_last_n])
        short_ttl_ids = [str(op_id) for op_id in all_ids[self._keep_last_n :]]

        pipe = self._redis_client.pipeline()

        # Ensure last N operations have long TTL (remove expire if present)
        for op_id in keep_long_ids:
            item_key = self._operation_key(op_id)
            pipe.persist(item_key)

        # Set short TTL on older operations
        for op_id in short_ttl_ids:
            item_key = self._operation_key(op_id)
            pipe.expire(item_key, self._ttl_seconds_short)

        # Ensure index key has long TTL (updated on each operation)
        pipe.expire(index_key, self._ttl_seconds_long)

        pipe.execute()

    def _get_redis(self, operation_id: str) -> Optional[Dict[str, Any]]:
        if not self._redis_client:
            return None
        raw = self._redis_client.get(self._operation_key(operation_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def create(
        self,
        operation_type: str,
        target_name: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        operation_id = str(uuid.uuid4())
        created_at = _utc_now_iso()
        item = {
            "id": operation_id,
            "type": operation_type,
            "target_name": target_name,
            "status": "queued",
            "progress": "accepted",
            "message": message,
            "details": details or {},
            "created_at": created_at,
            "updated_at": created_at,
            "updated_at_ts": time.time(),
            "completed_at": None,
            "timeline": [
                {
                    "status": "queued",
                    "progress": "accepted",
                    "message": message,
                    "timestamp": created_at,
                }
            ],
        }

        if self._redis_client:
            self._save_redis(item)
            self._cleanup_old_operations(target_name)
            return deepcopy(item)

        with self._lock:
            self._purge_expired_locked()
            self._items[operation_id] = item
            return deepcopy(item)

    def get(self, operation_id: str) -> Optional[Dict[str, Any]]:
        if self._redis_client:
            item = self._get_redis(operation_id)
            return deepcopy(item) if item else None

        with self._lock:
            self._purge_expired_locked()
            item = self._items.get(operation_id)
            return deepcopy(item) if item else None

    def append_event(
        self,
        operation_id: str,
        *,
        status: str,
        progress: str,
        message: str,
        details_patch: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        now_iso = _utc_now_iso()
        now_ts = time.time()

        if self._redis_client:
            item = self._get_redis(operation_id)
            if not item:
                return None
            timeline: List[Dict[str, Any]] = item.get("timeline", [])
            last_event = timeline[-1] if timeline else None
            same_core_state = bool(
                last_event
                and last_event.get("status") == status
                and last_event.get("progress") == progress
                and last_event.get("message") == message
            )
            if same_core_state and not details_patch:
                return deepcopy(item)
            if not last_event or (
                last_event.get("status") != status
                or last_event.get("progress") != progress
                or last_event.get("message") != message
            ):
                timeline.append(
                    {
                        "status": status,
                        "progress": progress,
                        "message": message,
                        "timestamp": now_iso,
                    }
                )
                item["timeline"] = timeline
            item["status"] = status
            item["progress"] = progress
            item["message"] = message
            if details_patch:
                details = dict(item.get("details") or {})
                details.update(details_patch)
                item["details"] = details
            item["updated_at"] = now_iso
            item["updated_at_ts"] = now_ts
            if status in {"succeeded", "failed"}:
                item["completed_at"] = now_iso
            self._save_redis(item)
            self._cleanup_old_operations(item.get("target_name") or "")
            return deepcopy(item)

        with self._lock:
            self._purge_expired_locked()
            item = self._items.get(operation_id)
            if not item:
                return None
            timeline = item.get("timeline", [])
            last_event = timeline[-1] if timeline else None
            same_core_state = bool(
                last_event
                and last_event.get("status") == status
                and last_event.get("progress") == progress
                and last_event.get("message") == message
            )
            if same_core_state and not details_patch:
                return deepcopy(item)
            if not last_event or (
                last_event.get("status") != status
                or last_event.get("progress") != progress
                or last_event.get("message") != message
            ):
                timeline.append(
                    {
                        "status": status,
                        "progress": progress,
                        "message": message,
                        "timestamp": now_iso,
                    }
                )
                item["timeline"] = timeline
            item["status"] = status
            item["progress"] = progress
            item["message"] = message
            if details_patch:
                details = dict(item.get("details") or {})
                details.update(details_patch)
                item["details"] = details
            item["updated_at"] = now_iso
            item["updated_at_ts"] = now_ts
            if status in {"succeeded", "failed"}:
                item["completed_at"] = now_iso
            return deepcopy(item)

    def list_recent(self, target_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        max_items = max(1, min(limit, 100))
        if self._redis_client:
            index_key = self._target_index_key(target_name)
            ids = self._redis_client.zrevrange(index_key, 0, max_items - 1) or []
            if not ids:
                return []
            keys = [self._operation_key(str(operation_id)) for operation_id in ids]
            raw_items = self._redis_client.mget(keys) or []
            result: List[Dict[str, Any]] = []
            stale_ids: List[str] = []
            for operation_id, raw in zip(ids, raw_items):
                if not raw:
                    stale_ids.append(str(operation_id))
                    continue
                try:
                    result.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    stale_ids.append(str(operation_id))
            if stale_ids:
                self._redis_client.zrem(index_key, *stale_ids)
            return result

        with self._lock:
            self._purge_expired_locked()
            items = [
                deepcopy(item)
                for item in self._items.values()
                if str(item.get("target_name") or "") == target_name
            ]
            items.sort(key=lambda x: float(x.get("updated_at_ts") or 0), reverse=True)
            return items[:max_items]
