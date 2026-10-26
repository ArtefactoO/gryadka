"""
Argo Rollout API endpoints.
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_permission
from app.audit import audit_log
from app.config import settings, get_app_config
from app.monitor_logging import log_collector_progress
from app.models.argo_rollout import (
    ArgoRolloutList,
    DeploymentInfo,
    RolloutInfo,
    RolloutOperationResponse,
)
from app.services.argo_rollout_service import get_argo_rollout_service
from app.services.cache import CacheService

logger = logging.getLogger(__name__)
router = APIRouter()

# Cache for reading Argo Rollout data (written by celery worker)
argo_rollout_cache = CacheService(
    db=settings.redis_rollouts_db if settings.redis_rollouts_db is not None else settings.redis_db
)

# Fallback collection timeout
FALLBACK_COLLECTION_TIMEOUT_S = 8


def _resolve_namespace(target_name: str, namespace: Optional[str]) -> str:
    current_config = get_app_config()
    targets = current_config.get_targets_by_type("argo-rollout") if current_config else []
    target = next((item for item in targets if item.name == target_name), None)
    if target is None:
        raise HTTPException(404, "Rollout target is not configured")
    configured = target.options.get("namespace", "default")
    if namespace is not None and namespace != configured:
        raise HTTPException(403, "Namespace is outside the configured target")
    return configured


async def _get_initialized_service(target_name: str, namespace: Optional[str]):
    resolved_namespace = _resolve_namespace(target_name, namespace)
    service = await get_argo_rollout_service()
    await service.initialize(resolved_namespace)
    return service, resolved_namespace


async def _collect_data_sync(target_name: str, namespace: str) -> Optional[dict]:
    """Synchronously collect Argo Rollout data as fallback."""
    try:
        from app.services.argo_rollout_collector import get_argo_rollout_collector
        collector = get_argo_rollout_collector()

        # Run collection in thread pool to not block
        loop = asyncio.get_event_loop()
        data = await asyncio.wait_for(
            loop.run_in_executor(None, collector.collect_target_data, namespace),
            timeout=FALLBACK_COLLECTION_TIMEOUT_S
        )

        if data:
            # Cache the fresh data
            cache_key = f"argo_rollout:target:{target_name}"
            argo_rollout_cache.redis_client.setex(
                cache_key,
                120,  # Use same TTL as celery task
                json.dumps(data, default=str)
            )
            log_collector_progress(logger, "argo-rollout", target_name, "api_fallback_cache_success", namespace=namespace)

        return data
    except asyncio.TimeoutError:
        logger.warning("Fallback collection timeout for %s", target_name)
        return None
    except Exception as e:
        logger.error("Fallback collection failed for %s: %s", target_name, e)
        return None


@router.get("/api/argo_rollout/{target_name}", response_model=ArgoRolloutList)
async def get_rollouts(
    target_name: str,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
    force_refresh: bool = Query(False, description="Force cache refresh"),
    live: bool = Query(False, description="Fetch live data from K8s API (no cache)"),
):
    """
    Get all rollouts and deployments in namespace.

    Returns:
        - rollouts: List of Argo Rollouts with status and history
        - deployments: List of Deployments without Rollout
    """
    try:
        # Get namespace from config if not provided
        namespace = _resolve_namespace(target_name, namespace)

        # If live mode requested, fetch directly from K8s API
        if live:
            log_collector_progress(logger, "argo-rollout", target_name, "api_live_collect", namespace=namespace)
            live_data = await _collect_data_sync(target_name, namespace)
            if live_data:
                from app.models.argo_rollout import RolloutInfo, DeploymentInfo
                all_rollouts = [RolloutInfo(**r) for r in live_data.get("rollouts", [])]
                all_deployments = [DeploymentInfo(**d) for d in live_data.get("deployments", [])]

                if target_name == 'deployment':
                    return ArgoRolloutList(rollouts=[], deployments=all_deployments)
                else:
                    return ArgoRolloutList(rollouts=all_rollouts, deployments=[])

        # Try to read from Redis cache first (populated by celery worker)
        cache_key = f"argo_rollout:target:{target_name}"
        cached_data = argo_rollout_cache.redis_client.get(cache_key)

        if cached_data and not force_refresh:
            try:
                data = json.loads(cached_data)
                # Convert dict rollouts/deployments back to models
                from app.models.argo_rollout import RolloutInfo, DeploymentInfo
                all_rollouts = [RolloutInfo(**r) for r in data.get("rollouts", [])]
                all_deployments = [DeploymentInfo(**d) for d in data.get("deployments", [])]

                # Filter based on target_name
                if target_name == 'deployment':
                    # For deployment target, show only deployments
                    return ArgoRolloutList(rollouts=[], deployments=all_deployments)
                else:
                    # For argo-rollout target, show only rollouts
                    return ArgoRolloutList(rollouts=all_rollouts, deployments=[])
            except Exception as e:
                logger.warning("Error parsing cached argo rollout data: %s", e)

        # Cache miss or force_refresh - try fallback collection
        log_collector_progress(logger, "argo-rollout", target_name, "api_cache_miss_collect", namespace=namespace)

        # Try synchronous fallback collection
        fallback_data = await _collect_data_sync(target_name, namespace)

        if fallback_data:
            from app.models.argo_rollout import RolloutInfo, DeploymentInfo
            all_rollouts = [RolloutInfo(**r) for r in fallback_data.get("rollouts", [])]
            all_deployments = [DeploymentInfo(**d) for d in fallback_data.get("deployments", [])]

            # Filter based on target_name
            if target_name == 'deployment':
                return ArgoRolloutList(rollouts=[], deployments=all_deployments)
            else:
                return ArgoRolloutList(rollouts=all_rollouts, deployments=[])

        # If fallback also failed, try service as last resort
        logger.warning("Fallback collection failed for %s, trying service", target_name)
        service, _ = await _get_initialized_service(target_name, namespace)
        result = await service.get_all(force_refresh=True)
        return result

    except Exception as e:
        logger.error("Error getting rollouts for %s: %s", target_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")


@router.get("/api/argo_rollout/{target_name}/{rollout_name}", response_model=RolloutInfo)
async def get_rollout_detail(
    target_name: str,
    rollout_name: str,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
):
    """Get detailed info for specific rollout."""
    try:
        _, resolved_namespace = await _get_initialized_service(target_name, namespace)
        from app.services.argo_rollout_collector import get_argo_rollout_collector
        detail = await asyncio.to_thread(
            get_argo_rollout_collector().get_rollout_detail,
            resolved_namespace,
            rollout_name,
        )
        if detail:
            return detail
        raise HTTPException(status_code=404, detail=f"Rollout {rollout_name} not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting rollout detail %s: %s", rollout_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")


@router.get("/api/argo_rollout/{target_name}/deployment/{deployment_name}", response_model=DeploymentInfo)
async def get_deployment_detail(
    target_name: str,
    deployment_name: str,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
):
    try:
        _, resolved_namespace = await _get_initialized_service(target_name, namespace)
        from app.services.argo_rollout_collector import get_argo_rollout_collector
        detail = await asyncio.to_thread(
            get_argo_rollout_collector().get_deployment_detail,
            resolved_namespace,
            deployment_name,
        )
        if detail:
            return detail
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_name} not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting deployment detail %s: %s", deployment_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")


@router.post(
    "/api/argo_rollout/{target_name}/{rollout_name}/promote",
    response_model=RolloutOperationResponse
)
async def promote_rollout(
    target_name: str,
    rollout_name: str,
    request: Request,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
    user=Depends(require_permission("argo_rollout.promote")),
):
    """
    Promote rollout (continue canary deployment).

    Requires manage_rollouts permission.
    """
    try:
        service, resolved_namespace = await _get_initialized_service(target_name, namespace)
        result = await service.promote_rollout(rollout_name)

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        audit_log(
            "argo_rollout_promote",
            user=user,
            request=request,
            permission="argo_rollout.promote",
            target_type="argo-rollout",
            target_name=target_name,
            namespace=resolved_namespace,
            rollout_name=rollout_name,
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error promoting rollout %s: %s", rollout_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")


@router.post(
    "/api/argo_rollout/{target_name}/{rollout_name}/abort",
    response_model=RolloutOperationResponse
)
async def abort_rollout(
    target_name: str,
    rollout_name: str,
    request: Request,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
    user=Depends(require_permission("argo_rollout.promote")),
):
    """
    Abort rollout.

    Requires manage_rollouts permission.
    """
    try:
        service, resolved_namespace = await _get_initialized_service(target_name, namespace)
        result = await service.abort_rollout(rollout_name)

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        audit_log(
            "argo_rollout_abort",
            user=user,
            request=request,
            permission="argo_rollout.promote",
            target_type="argo-rollout",
            target_name=target_name,
            namespace=resolved_namespace,
            rollout_name=rollout_name,
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error aborting rollout %s: %s", rollout_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")


@router.post(
    "/api/argo_rollout/{target_name}/deployment/{deployment_name}/restart",
    response_model=RolloutOperationResponse
)
async def restart_deployment(
    target_name: str,
    deployment_name: str,
    request: Request,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
    user=Depends(require_permission("argo_rollout.restart")),
):
    """
    Restart deployment (for services without Argo Rollout).

    Requires manage_rollouts permission.
    """
    try:
        service, resolved_namespace = await _get_initialized_service(target_name, namespace)
        result = await service.restart_deployment(deployment_name)

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        audit_log(
            "argo_rollout_deployment_restart",
            user=user,
            request=request,
            permission="argo_rollout.restart",
            target_type="argo-rollout",
            target_name=target_name,
            namespace=resolved_namespace,
            deployment_name=deployment_name,
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error restarting deployment %s: %s", deployment_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")


@router.post(
    "/api/argo_rollout/{target_name}/{rollout_name}/restart",
    response_model=RolloutOperationResponse
)
async def restart_rollout(
    target_name: str,
    rollout_name: str,
    request: Request,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
    user=Depends(require_permission("argo_rollout.restart")),
):
    try:
        service, resolved_namespace = await _get_initialized_service(target_name, namespace)
        result = await service.restart_rollout(rollout_name)
        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        audit_log(
            "argo_rollout_restart",
            user=user,
            request=request,
            permission="argo_rollout.restart",
            target_type="argo-rollout",
            target_name=target_name,
            namespace=resolved_namespace,
            rollout_name=rollout_name,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error restarting rollout %s: %s", rollout_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")


@router.post(
    "/api/argo_rollout/{target_name}/{rollout_name}/promote-full",
    response_model=RolloutOperationResponse
)
async def promote_full_rollout(
    target_name: str,
    rollout_name: str,
    request: Request,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
    user=Depends(require_permission("argo_rollout.promote")),
):
    try:
        service, resolved_namespace = await _get_initialized_service(target_name, namespace)
        result = await service.promote_full_rollout(rollout_name)
        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        audit_log(
            "argo_rollout_promote_full",
            user=user,
            request=request,
            permission="argo_rollout.promote",
            target_type="argo-rollout",
            target_name=target_name,
            namespace=resolved_namespace,
            rollout_name=rollout_name,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error promoting rollout full %s: %s", rollout_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")


@router.post(
    "/api/argo_rollout/{target_name}/{rollout_name}/retry",
    response_model=RolloutOperationResponse
)
async def retry_rollout(
    target_name: str,
    rollout_name: str,
    request: Request,
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
    user=Depends(require_permission("argo_rollout.restart")),
):
    try:
        service, resolved_namespace = await _get_initialized_service(target_name, namespace)
        result = await service.retry_rollout(rollout_name)
        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        audit_log(
            "argo_rollout_retry",
            user=user,
            request=request,
            permission="argo_rollout.restart",
            target_type="argo-rollout",
            target_name=target_name,
            namespace=resolved_namespace,
            rollout_name=rollout_name,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error retrying rollout %s: %s", rollout_name, e)
        raise HTTPException(status_code=500, detail="Monitoring operation failed")
