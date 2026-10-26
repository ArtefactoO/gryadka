"""
Server-Sent Events manager with optional Redis pub/sub fanout.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SSESubscriber:
    queue: asyncio.Queue
    target_type: Optional[str] = None
    target_name: Optional[str] = None
    event: Optional[str] = None


class SSEManager:
    """In-process SSE broker with Redis fanout support."""

    def __init__(self, redis_channel: str = "gryadka:sse:events") -> None:
        self._subscribers: Dict[str, SSESubscriber] = {}
        self._lock = asyncio.Lock()
        self._redis_client: Optional[Any] = None
        self._redis_channel = redis_channel
        self._redis_listener_started = False
        self._redis_listener_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def configure_redis(self, redis_client: Any) -> None:
        self._redis_client = redis_client

    async def start(self) -> None:
        if not self._redis_client:
            return
        if self._redis_listener_started:
            return
        with self._redis_listener_lock:
            if self._redis_listener_started:
                return
            self._loop = asyncio.get_running_loop()
            self._lock = asyncio.Lock()
            self._stop.clear()
            self._thread = threading.Thread(target=self._redis_listen_loop, daemon=True)
            self._redis_listener_started = True
            self._thread.start()
            logger.info("SSE Redis listener started")

    async def stop(self) -> None:
        self._stop.set()
        if self._thread:
            await asyncio.to_thread(self._thread.join, 2)
        self._redis_listener_started = False
        self._loop = None
        self._subscribers.clear()

    async def subscribe(
        self,
        subscriber_id: str,
        *,
        target_type: Optional[str] = None,
        target_name: Optional[str] = None,
        event: Optional[str] = None,
    ) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[subscriber_id] = SSESubscriber(
                queue=queue,
                target_type=target_type,
                target_name=target_name,
                event=event,
            )
        return queue

    async def unsubscribe(self, subscriber_id: str) -> None:
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def _matches(self, subscriber: SSESubscriber, payload: Dict[str, Any]) -> bool:
        if subscriber.event and subscriber.event != str(payload.get("event") or ""):
            return False
        if subscriber.target_type and subscriber.target_type != str(payload.get("target_type") or ""):
            return False
        if subscriber.target_name and subscriber.target_name != str(payload.get("target_name") or ""):
            return False
        return True

    async def _publish_local(self, payload: Dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.values())
        for subscriber in subscribers:
            if not self._matches(subscriber, payload):
                continue
            if subscriber.queue.full():
                try:
                    subscriber.queue.get_nowait()
                except Exception:
                    pass
            try:
                subscriber.queue.put_nowait(payload)
            except Exception:
                continue

    async def publish(self, payload: Dict[str, Any], *, fanout: bool = True) -> None:
        event_payload = dict(payload)
        if "timestamp" not in event_payload:
            event_payload["timestamp"] = _utc_now_iso()
        if fanout and self._redis_client:
            try:
                await asyncio.to_thread(self._redis_client.publish, self._redis_channel, json.dumps(event_payload, default=str))
                return
            except Exception as exc:
                logger.warning("SSE fanout unavailable: error_type=%s", type(exc).__name__)
        await self._publish_local(event_payload)


    def publish_background(self, payload: Dict[str, Any], *, fanout: bool = True) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(payload, fanout=fanout))
        except RuntimeError:
            # No running loop (e.g. sync context). Best-effort: skip async dispatch.
            pass

    def _schedule_local_dispatch(self, payload: Dict[str, Any]) -> None:
        try:
            loop = self._loop or asyncio.get_running_loop()
            loop.create_task(self._publish_local(payload))
        except Exception:
            pass

    def _redis_listen_loop(self) -> None:
        if not self._redis_client:
            return
        while not self._stop.is_set():
            pubsub = self._redis_client.pubsub(ignore_subscribe_messages=True)
            try:
                pubsub.subscribe(self._redis_channel)
                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=1)
                    if not isinstance(message, dict) or message.get('type') != 'message':
                        continue
                    try:
                        payload = json.loads(message['data'])
                    except (ValueError, KeyError):
                        continue
                    if isinstance(payload, dict) and self._loop and not self._loop.is_closed():
                        payload.setdefault('timestamp', _utc_now_iso())
                        self._loop.call_soon_threadsafe(self._schedule_local_dispatch, payload)
            except Exception as exc:
                if not self._stop.is_set():
                    logger.warning('SSE listener reconnecting: error_type=%s', type(exc).__name__)
                    self._stop.wait(1)
            finally:
                pubsub.close()


sse_manager = SSEManager()
