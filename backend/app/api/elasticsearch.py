"""
API endpoints for Elasticsearch cluster state and utilization.
"""
import logging
from fastapi import APIRouter, HTTPException, status

from app.config import get_app_config, settings
from app.services.cache import CacheService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/elasticsearch", tags=["elasticsearch"])
logging_cache = CacheService(
    db=settings.redis_logging_db if settings.redis_logging_db is not None else settings.redis_db
)


@router.get("/{target_name}")
async def get_elasticsearch_target(target_name: str):
    """Get Elasticsearch cluster state and utilization for a specific target."""
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuration is not initialized yet",
        )

    es_target = next(
        (t for t in current_config.elasticsearch_targets if t.name == target_name),
        None,
    )
    if not es_target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Elasticsearch target {target_name} not found",
        )

    cache_key = f"elasticsearch:target:{target_name}"
    data = logging_cache.get_json(cache_key)
    if data is not None:
        if not isinstance(data, dict):
            logger.warning("Invalid Elasticsearch cached payload type for target %s", target_name)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Elasticsearch data is warming up. Values will appear in a few seconds.",
            )
        ui_url = es_target.options.get("ui_url") if es_target.options else None
        monitoring_url = es_target.options.get("monitoring_url") if es_target.options else None
        if ui_url is not None:
            data["ui_url"] = ui_url
        if monitoring_url is not None:
            data["monitoring_url"] = monitoring_url
        return data

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Elasticsearch data is warming up. Values will appear in a few seconds.",
    )
