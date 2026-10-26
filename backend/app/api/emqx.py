"""
API endpoints for EMQX clusters.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, status
from redis.exceptions import RedisError

from app.config import get_app_config, settings
from app.services.cache import CacheService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/emqx", tags=["emqx"])
emqx_cache = CacheService(
    db=settings.redis_emqx_db if settings.redis_emqx_db is not None else settings.redis_db
)


@router.get("/{target_name}")
async def get_emqx_target(target_name: str):
    """Get EMQX cluster state and metrics for a specific target."""
    return await asyncio.to_thread(_get_emqx_target_payload, target_name)


def _get_emqx_target_payload(target_name: str):
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuration is not initialized yet",
        )

    emqx_target = next((t for t in current_config.emqx_targets if t.name == target_name), None)
    if not emqx_target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EMQX target {target_name} not found",
        )

    cache_key = f"emqx:target:{target_name}"
    try:
        cached = emqx_cache.redis_client.get(cache_key)
    except RedisError as exc:
        logger.warning("Could not read EMQX target %s from cache: %s", target_name, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EMQX data is temporarily unavailable. Try again in a few seconds.",
        ) from exc

    if cached:
        try:
            data = json.loads(cached)
        except (TypeError, json.JSONDecodeError) as exc:
            logger.warning("Invalid EMQX cached payload for target %s: %s", target_name, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="EMQX data is temporarily unavailable. Try again in a few seconds.",
            ) from exc
        ui_url = emqx_target.options.get("ui_url") if emqx_target.options else None
        if ui_url is not None:
            data["ui_url"] = ui_url
        return data

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="EMQX data is warming up. Values will appear in a few seconds.",
    )
