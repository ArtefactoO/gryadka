"""
Health check endpoints
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.config import get_app_config
from app.services.cache import CacheService

router = APIRouter(tags=["health"])

cache = CacheService()


@router.get("/api/health")
async def health_check():
    """Health check endpoint"""
    redis_ok = cache.ping()
    return {
        "status": "healthy" if redis_ok else "degraded",
        "redis": "connected" if redis_ok else "disconnected"
    }


@router.get("/api/ready")
async def readiness_check():
    """Kubernetes readiness endpoint: app is serving and runtime config is loaded."""
    config_loaded = get_app_config() is not None
    status_code = 200 if config_loaded else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if config_loaded else "not_ready",
            "config": "loaded" if config_loaded else "missing",
        },
    )
