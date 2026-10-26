"""Shared target cache, API reads and bounded collection orchestration."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import logging
import json
from decimal import Decimal
from typing import Callable, Iterable

from fastapi import HTTPException

from app.config import get_app_config, settings
from app.services.cache import CacheService

logger = logging.getLogger(__name__)


@lru_cache(maxsize=16)
def get_monitor_cache(monitor_type: str) -> CacheService:
    db = getattr(settings, f"redis_{monitor_type}_db", None)
    return CacheService(db=settings.redis_db if db is None else db)


def get_configured_target(monitor_type: str, name: str):
    config = get_app_config()
    if config is None:
        raise HTTPException(503, "Конфигурация мониторинга пока не загружена. Попробуйте обновить монитор позже.")
    target = next((t for t in config.get_targets_by_type(monitor_type) if t.name == name), None)
    if target is None:
        raise HTTPException(404, "Монитор не найден в конфигурации. Обновите список мониторов.")
    return target


def read_cached_target(monitor_type: str, name: str) -> dict:
    get_configured_target(monitor_type, name)
    payload = get_monitor_cache(monitor_type).get_json(f"{monitor_type}:target:{name}")
    if not isinstance(payload, dict):
        raise HTTPException(503, "Свежие данные мониторинга пока не получены. Попробуйте обновить монитор позже.")
    return payload


def read_cached_databases_overview(monitor_type: str) -> dict[str, list[str]]:
    """Names from configured targets only. Never connect to a database for search."""
    config = get_app_config()
    if config is None:
        raise HTTPException(503, "Конфигурация мониторинга пока не загружена.")
    cache = get_monitor_cache(monitor_type)
    overview = {}
    for target in config.get_targets_by_type(monitor_type):
        payload = cache.get_json(f"{monitor_type}:target:{target.name}")
        nodes = payload.get('nodes', []) if isinstance(payload, dict) else []
        overview[target.name] = sorted({db['name'] for node in nodes
                                        for db in node.get('databases', [])
                                        if isinstance(db.get('name'), str)})
    return overview


def cache_target(monitor_type: str, name: str, payload: dict) -> None:
    config = get_app_config()
    interval = config.update_interval_s if config else settings.update_interval
    get_monitor_cache(monitor_type).redis_client.setex(f"{monitor_type}:target:{name}", max(60, interval * 3),
        json.dumps(payload, default=lambda value: float(value) if isinstance(value, Decimal) else str(value)))


def collect_target_batch(targets: Iterable, collect_one: Callable, cache_one: Callable, notify: Callable, monitor_type: str) -> list[str]:
    targets = list(targets)
    if not targets:
        return []
    updated = []
    with ThreadPoolExecutor(max_workers=min(len(targets), 8)) as executor:
        futures = [executor.submit(collect_one, target.name) for target in targets]
        for future in as_completed(futures):
            try:
                name, payload = future.result()
                if payload is not None:
                    cache_one(name, payload)
                    updated.append(name)
            except Exception as exc:
                logger.warning('Target batch item failed: monitor=%s error_type=%s', monitor_type, type(exc).__name__)
    notify([f"{monitor_type}:{name}" for name in updated])
    return updated
