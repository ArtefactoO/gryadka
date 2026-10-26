import asyncio
"""
Redis API endpoints.
"""
import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from redis.exceptions import NoPermissionError

from app.services.monitor_runtime import read_cached_target
from app.api.auth import require_permission
from app.audit import audit_log
from app.config import get_app_config, settings
from app.models.redis import RedisCluster
from app.services.cache import CacheService
from app.services.redis_service import redis_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/redis", tags=["redis"])

class RedisKeyItem(BaseModel):
    key: str
    type: str
    ttl: int

class ScanResponse(BaseModel):
    cursor: int
    keys: List[RedisKeyItem]

class DeleteKeysRequest(BaseModel):
    keys: List[str] = []
    pattern: Optional[str] = None
    db: int = 0

cache = CacheService(
    db=(
        settings.redis_redis_db
        if settings.redis_redis_db is not None
        else (
            settings.redis_cache_db
            if settings.redis_cache_db is not None
            else settings.redis_db
        )
    )
)


@router.get("/{target_name}", response_model=RedisCluster)
async def get_redis_target(target_name: str):
    """Get Redis target metrics (overview, nodes, databases)."""
    return await asyncio.to_thread(read_cached_target, "redis", target_name)


@router.get("/{target_name}/keys", response_model=ScanResponse)
async def scan_keys(
    target_name: str,
    pattern: str = Query(..., min_length=1),
    cursor: int = Query(0, ge=0),
    count: int = Query(100, ge=1, le=1000),
    db: int = Query(0, ge=0),
    current_user: dict = Depends(require_permission("redis.keys.read"))
):
    """Scan keys by pattern."""
    try:
        username = current_user.get("sub", "Someone")
        next_cursor, keys = await redis_service.scan_keys(target_name, pattern, cursor, count, db, username=username)
        return ScanResponse(cursor=next_cursor, keys=keys)
    except NoPermissionError:
        raise HTTPException(status_code=403, detail='Поиск ключей недоступен: учетной записи Redis не разрешены SCAN, TYPE, TTL или выбор базы. Обратитесь к администратору за правами на поиск. Мониторинг INFO доступен отдельно.') from None
    except Exception as e:
        logger.error(f"Error in scan_keys: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Операция мониторинга не выполнена. Проверьте доступность сервиса и права учетной записи.")


@router.delete("/{target_name}/keys")
async def delete_keys(
    target_name: str,
    request: DeleteKeysRequest,
    http_request: Request,
    current_user: dict = Depends(require_permission("redis.keys.delete"))
):
    """Delete specific keys or keys matching a pattern."""
    try:
        username = current_user.get("sub", "Someone")
        if request.pattern:
            count = await redis_service.delete_keys_by_pattern(target_name, request.pattern, request.db, username=username)
        else:
            count = await redis_service.delete_keys(target_name, request.keys, request.db, username=username)
        audit_log(
            "redis_keys_delete",
            user=current_user,
            request=http_request,
            permission="redis.keys.delete",
            target_type="redis",
            target_name=target_name,
            db=request.db,
            key_count=len(request.keys or []),
            pattern=request.pattern,
            deleted=count,
        )
        return {"deleted": count}
    except NoPermissionError:
        raise HTTPException(status_code=403, detail='Учетной записи Redis не разрешено удаление ключей. Обратитесь к администратору.') from None
    except Exception as e:
        logger.error(f"Error in delete_keys: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Операция мониторинга не выполнена. Проверьте доступность сервиса и права учетной записи.")


@router.get("/{target_name}/keys/{key:path}/value")
async def get_key_value(
    target_name: str,
    key: str,
    db: int = Query(0, ge=0),
    _: dict = Depends(require_permission("redis.keys.read"))
):
    """Get value of a specific key with type-specific formatting."""
    try:
        result = await redis_service.get_key_value(target_name, key, db)
        return result
    except NoPermissionError:
        raise HTTPException(status_code=403, detail='Учетной записи Redis не разрешено чтение значения ключа. Обратитесь к администратору.') from None
    except Exception as e:
        logger.error(f"Error in get_key_value: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Операция мониторинга не выполнена. Проверьте доступность сервиса и права учетной записи.")
