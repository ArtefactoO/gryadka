"""Shared Redis client options for Gryadka internal Redis."""
from typing import Any, Dict, Optional

import redis

from app.config import settings


def _build_retry() -> Optional[Any]:
    try:
        from redis.backoff import ExponentialBackoff
        from redis.retry import Retry
    except Exception:
        return None

    attempts = max(0, int(settings.redis_retry_attempts))
    if attempts <= 0:
        return None
    return Retry(
        ExponentialBackoff(
            cap=float(settings.redis_retry_cap_delay_s),
            base=float(settings.redis_retry_base_delay_s),
        ),
        attempts,
    )


def build_internal_redis_kwargs(db: int, decode_responses: bool = True) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "host": settings.redis_host,
        "port": settings.redis_port,
        "password": settings.redis_password,
        "db": db,
        "decode_responses": decode_responses,
        "socket_connect_timeout": float(settings.redis_socket_connect_timeout_s),
        "socket_timeout": float(settings.redis_socket_timeout_s),
        "socket_keepalive": True,
        "health_check_interval": max(0, int(settings.redis_health_check_interval_s)),
        "retry_on_timeout": bool(settings.redis_retry_on_timeout),
    }
    retry = _build_retry()
    if retry is not None:
        kwargs["retry"] = retry
    return kwargs


def create_internal_redis_client(db: int, decode_responses: bool = True) -> redis.Redis:
    return redis.Redis(**build_internal_redis_kwargs(db=db, decode_responses=decode_responses))
