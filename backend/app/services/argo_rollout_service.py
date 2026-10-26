"""
Service layer for Argo Rollout operations.
Handles caching, SSE notifications, and business logic.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.argo_rollout import (
    ArgoRolloutList,
    DeploymentInfo,
    RolloutInfo,
    RolloutOperationResponse,
)
from app.services.argo_rollout_collector import ArgoRolloutCollector
from app.services.cache import CacheService
from app.services.sse_manager import sse_manager
from app.config import settings

logger = logging.getLogger(__name__)


class ArgoRolloutService:
    """Service for Argo Rollout operations with caching and SSE."""

    CACHE_PREFIX = "argo_rollout"
    CACHE_TTL = 60  # 1 minute cache for rollouts

    def __init__(self):
        self.collector = ArgoRolloutCollector()
        self._watch_task: Optional[asyncio.Task] = None
        self._namespace: Optional[str] = None
        self._cache = CacheService(
            db=settings.redis_rollouts_db if settings.redis_rollouts_db is not None else settings.redis_db
        )

    async def initialize(self, namespace: str):
        """Initialize service and start watch."""
        if self._namespace == namespace and self._watch_task and not self._watch_task.done():
            return
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        self._namespace = namespace
        self._watch_task = asyncio.create_task(self._watch_rollouts())
        logger.info("ArgoRolloutService initialized for namespace: %s", namespace)

    async def shutdown(self):
        """Shutdown service and stop watch."""
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            logger.info("ArgoRolloutService shutdown")

    async def _watch_rollouts(self):
        """Background task to watch rollout changes."""
        while True:
            try:
                # Run watch in thread pool since it's blocking
                loop = asyncio.get_event_loop()
                for event in await loop.run_in_executor(
                    None,
                    lambda: list(self.collector.watch_rollouts(self._namespace, timeout=300))
                ):
                    event_type = event.get("type")
                    rollout = event.get("object")

                    if rollout:
                        rollout_name = rollout.get("metadata", {}).get("name", "unknown")
                        logger.debug("Rollout event: %s - %s", event_type, rollout_name)

                        # Invalidate cache
                        await self._invalidate_cache()

                        # Send SSE event
                        await sse_manager.publish({
                            "event": "argo_rollout.status_changed",
                            "target_type": "argo_rollout",
                            "target_name": rollout_name,
                            "namespace": self._namespace,
                            "event_type": event_type,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })

            except Exception as e:
                logger.error("Error in rollout watch: %s", e)
                # Wait before retry
                await asyncio.sleep(5)

    def _get_cache_key(self, suffix: str = "") -> str:
        """Generate cache key."""
        base = f"{self.CACHE_PREFIX}:{self._namespace}"
        return f"{base}:{suffix}" if suffix else base

    async def _invalidate_cache(self):
        """Invalidate rollout cache."""
        try:
            pattern = f"{self.CACHE_PREFIX}:{self._namespace}:*"
            # Delete all keys matching pattern
            # Note: This is a simple implementation, for production use Redis SCAN
            keys = self._cache.redis_client.keys(pattern)
            if keys:
                self._cache.redis_client.delete(*keys)
        except Exception as e:
            logger.error("Error invalidating cache: %s", e)

    async def get_rollouts(self, force_refresh: bool = False) -> List[RolloutInfo]:
        """Get all rollouts with caching."""
        cache_key = self._get_cache_key("rollouts")

        if not force_refresh:
            # Try to get from cache
            cached_data = self._cache.redis_client.get(cache_key)
            if cached_data:
                try:
                    import json
                    cached = json.loads(cached_data)
                    return [RolloutInfo(**item) for item in cached]
                except Exception as e:
                    logger.error("Error deserializing cached rollouts: %s", e)

        # Get from collector
        try:
            loop = asyncio.get_event_loop()
            rollouts = await loop.run_in_executor(
                None,
                self.collector.get_rollouts,
                self._namespace
            )

            # Cache the result
            try:
                import json
                self._cache.redis_client.setex(
                    cache_key,
                    self.CACHE_TTL,
                    json.dumps([r.model_dump() for r in rollouts], default=str)
                )
            except Exception as e:
                logger.error("Error caching rollouts: %s", e)

            return rollouts

        except Exception as e:
            logger.error("Error getting rollouts: %s", e)
            return []

    async def get_deployments(self, force_refresh: bool = False) -> List[DeploymentInfo]:
        """Get deployments without rollout."""
        cache_key = self._get_cache_key("deployments")

        if not force_refresh:
            cached_data = self._cache.redis_client.get(cache_key)
            if cached_data:
                try:
                    import json
                    cached = json.loads(cached_data)
                    return [DeploymentInfo(**item) for item in cached]
                except Exception as e:
                    logger.error("Error deserializing cached deployments: %s", e)

        try:
            loop = asyncio.get_event_loop()
            deployments = await loop.run_in_executor(
                None,
                self.collector.get_deployments_without_rollout,
                self._namespace
            )

            # Cache
            try:
                import json
                self._cache.redis_client.setex(
                    cache_key,
                    self.CACHE_TTL,
                    json.dumps([d.model_dump() for d in deployments], default=str)
                )
            except Exception as e:
                logger.error("Error caching deployments: %s", e)

            return deployments

        except Exception as e:
            logger.error("Error getting deployments: %s", e)
            return []

    async def get_all(self, force_refresh: bool = False) -> ArgoRolloutList:
        """Get all rollouts and deployments."""
        rollouts, deployments = await asyncio.gather(
            self.get_rollouts(force_refresh),
            self.get_deployments(force_refresh)
        )
        return ArgoRolloutList(rollouts=rollouts, deployments=deployments)

    async def promote_rollout(self, rollout_name: str) -> RolloutOperationResponse:
        """Promote rollout."""
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                self.collector.promote_rollout,
                self._namespace,
                rollout_name
            )

            if success:
                # Invalidate cache and notify
                await self._invalidate_cache()
                await sse_manager.publish({
                    "event": "argo_rollout.operation_completed",
                    "target_type": "argo_rollout",
                    "target_name": rollout_name,
                    "operation": "promote",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                return RolloutOperationResponse(
                    success=True,
                    message=f"Rollout {rollout_name} promoted successfully"
                )
            else:
                return RolloutOperationResponse(
                    success=False,
                    message=f"Failed to promote rollout {rollout_name}"
                )

        except Exception as e:
            logger.error("Error promoting rollout %s: %s", rollout_name, e)
            return RolloutOperationResponse(
                success=False,
                message=f"Error: {str(e)}"
            )

    async def abort_rollout(self, rollout_name: str) -> RolloutOperationResponse:
        """Abort rollout."""
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                self.collector.abort_rollout,
                self._namespace,
                rollout_name
            )

            if success:
                await self._invalidate_cache()
                await sse_manager.publish({
                    "event": "argo_rollout.operation_completed",
                    "target_type": "argo_rollout",
                    "target_name": rollout_name,
                    "operation": "abort",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                return RolloutOperationResponse(
                    success=True,
                    message=f"Rollout {rollout_name} aborted"
                )
            else:
                return RolloutOperationResponse(
                    success=False,
                    message=f"Failed to abort rollout {rollout_name}"
                )

        except Exception as e:
            logger.error("Error aborting rollout %s: %s", rollout_name, e)
            return RolloutOperationResponse(
                success=False,
                message=f"Error: {str(e)}"
            )

    async def promote_full_rollout(self, rollout_name: str) -> RolloutOperationResponse:
        """Promote rollout fully, skipping remaining steps."""
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                self.collector.promote_full_rollout,
                self._namespace,
                rollout_name
            )

            if success:
                await self._invalidate_cache()
                await sse_manager.publish({
                    "event": "argo_rollout.operation_completed",
                    "target_type": "argo_rollout",
                    "target_name": rollout_name,
                    "operation": "promote-full",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                return RolloutOperationResponse(
                    success=True,
                    message=f"Rollout {rollout_name} promoted fully"
                )
            return RolloutOperationResponse(
                success=False,
                message=f"Failed to promote rollout {rollout_name} fully"
            )
        except Exception as e:
            logger.error("Error promoting rollout full %s: %s", rollout_name, e)
            return RolloutOperationResponse(success=False, message=f"Error: {str(e)}")

    async def retry_rollout(self, rollout_name: str) -> RolloutOperationResponse:
        """Retry rollout after terminal/failed state."""
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                self.collector.retry_rollout,
                self._namespace,
                rollout_name
            )

            if success:
                await self._invalidate_cache()
                await sse_manager.publish({
                    "event": "argo_rollout.operation_completed",
                    "target_type": "argo_rollout",
                    "target_name": rollout_name,
                    "operation": "retry",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                return RolloutOperationResponse(
                    success=True,
                    message=f"Rollout {rollout_name} retried"
                )
            return RolloutOperationResponse(
                success=False,
                message=f"Failed to retry rollout {rollout_name}"
            )
        except Exception as e:
            logger.error("Error retrying rollout %s: %s", rollout_name, e)
            return RolloutOperationResponse(success=False, message=f"Error: {str(e)}")

    async def restart_rollout(self, rollout_name: str) -> RolloutOperationResponse:
        """Restart rollout pods."""
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                self.collector.restart_rollout,
                self._namespace,
                rollout_name
            )

            if success:
                await self._invalidate_cache()
                await sse_manager.publish({
                    "event": "argo_rollout.operation_completed",
                    "target_type": "argo_rollout",
                    "target_name": rollout_name,
                    "operation": "restart",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                return RolloutOperationResponse(
                    success=True,
                    message=f"Rollout {rollout_name} restarted"
                )
            return RolloutOperationResponse(
                success=False,
                message=f"Failed to restart rollout {rollout_name}"
            )
        except Exception as e:
            logger.error("Error restarting rollout %s: %s", rollout_name, e)
            return RolloutOperationResponse(success=False, message=f"Error: {str(e)}")

    async def restart_deployment(self, deployment_name: str) -> RolloutOperationResponse:
        """Restart deployment."""
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                self.collector.restart_deployment,
                self._namespace,
                deployment_name
            )

            if success:
                await self._invalidate_cache()
                await sse_manager.publish({
                    "event": "argo_rollout.operation_completed",
                    "target_type": "argo_rollout",
                    "target_name": deployment_name,
                    "operation": "restart",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                return RolloutOperationResponse(
                    success=True,
                    message=f"Deployment {deployment_name} restarted"
                )
            else:
                return RolloutOperationResponse(
                    success=False,
                    message=f"Failed to restart deployment {deployment_name}"
                )

        except Exception as e:
            logger.error("Error restarting deployment %s: %s", deployment_name, e)
            return RolloutOperationResponse(
                success=False,
                message=f"Error: {str(e)}"
            )


# Global service instance
_argo_rollout_service: Optional[ArgoRolloutService] = None


async def get_argo_rollout_service() -> ArgoRolloutService:
    """Get or create ArgoRolloutService instance."""
    global _argo_rollout_service
    if _argo_rollout_service is None:
        _argo_rollout_service = ArgoRolloutService()
    return _argo_rollout_service


async def initialize_argo_rollout_service(namespace: str):
    """Initialize service on startup."""
    service = await get_argo_rollout_service()
    await service.initialize(namespace)


async def shutdown_argo_rollout_service():
    """Shutdown service."""
    global _argo_rollout_service
    if _argo_rollout_service:
        await _argo_rollout_service.shutdown()
        _argo_rollout_service = None
