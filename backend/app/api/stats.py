"""
Statistics API endpoints
"""
from fastapi import APIRouter
from datetime import datetime
from app.models.cluster import ClusterStats
from app.services.cache import CacheService
from app.config import settings

router = APIRouter(prefix="/api/stats", tags=["stats"])

cache = CacheService(
    db=settings.redis_system_db
    if settings.redis_system_db is not None
    else (settings.redis_cache_db if settings.redis_cache_db is not None else settings.redis_db)
)


@router.get("", response_model=ClusterStats)
async def get_stats():
    """Get overall statistics"""
    stats_data = cache.get_stats()

    if not stats_data:
        return ClusterStats(
            total_clusters=0,
            total_nodes=0,
            last_update=datetime.utcnow()
        )

    return ClusterStats(
        total_clusters=stats_data.get('total_clusters', 0),
        total_nodes=stats_data.get('total_nodes', 0),
        last_update=datetime.fromisoformat(stats_data.get('last_update', datetime.utcnow().isoformat()))
    )
