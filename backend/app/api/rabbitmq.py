"""
RabbitMQ API endpoints.
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.auth import get_optional_user, require_permission
from app.audit import audit_log
from app.authorization import has_permission
from app.config import get_app_config, settings
from app.models.rabbitmq import RabbitCluster
from app.services.celery_app import MONITOR_SPECS, celery_app
from app.services.cache import CacheService
from app.services.rabbitmq_collector import RabbitMQCollector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rabbitmq", tags=["rabbitmq"])

cache = CacheService(
    db=settings.redis_rabbitmq_db if settings.redis_rabbitmq_db is not None else settings.redis_db
)
collector = RabbitMQCollector()


class RabbitQueueActionRequest(BaseModel):
    name: str = Field(..., min_length=1)
    vhost: str = "/"


class RabbitQueueActionResponse(BaseModel):
    status: str
    action: str
    target_name: str
    vhost: str
    queue: str
    refresh_task_id: str


@router.get("/{target_name}", response_model=RabbitCluster)
async def get_rabbitmq_target(
    target_name: str,
    current_user: Optional[dict] = Depends(get_optional_user)
):
    """Get RabbitMQ target metrics (overview, nodes, lightweight queues)."""
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Конфигурация пока не загружена.",
        )

    rabbit_target = next(
        (t for t in current_config.rabbitmq_targets if t.name == target_name),
        None,
    )
    if not rabbit_target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Монитор RabbitMQ {target_name} не найден.",
        )

    cache_key = f"rabbitmq:target:{target_name}"
    cached = cache.redis_client.get(cache_key)
    if cached:
        payload = json.loads(cached)
        # Users are only returned if user is authenticated
        users = None
        if has_permission(current_user, "rabbitmq.users.read"):
            users_data = payload.get("users")
            if users_data:
                from app.models.rabbitmq import RabbitUser
                users = [RabbitUser(**user_data) for user_data in users_data]

        ui_url = rabbit_target.options.get("ui_url") if rabbit_target.options else None
        return RabbitCluster(
            name=payload["name"],
            overview=payload["overview"],
            nodes=payload["nodes"],
            queues=payload["queues"],
            users=users,
            last_update=datetime.fromisoformat(payload["last_update"]),
            ui_url=ui_url,
        )

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Данные RabbitMQ собираются после перезапуска и появятся автоматически.",
    )


def _get_cached_queue_metrics(target_name: str, vhost: str, queue_name: str) -> dict:
    cached = cache.get_raw(f"rabbitmq:target:{target_name}")
    if not cached:
        return {}
    try:
        payload = json.loads(cached)
    except Exception:
        return {}
    for queue in payload.get("queues") or []:
        if queue.get("vhost") == vhost and queue.get("name") == queue_name:
            return {
                "messages": queue.get("messages"),
                "messages_ready": queue.get("messages_ready"),
                "messages_unacknowledged": queue.get("messages_unacknowledged"),
                "consumers": queue.get("consumers"),
            }
    return {}


def _queue_refresh(target_name: str):
    return celery_app.send_task(
        "refresh_target",
        args=["rabbitmq", target_name],
        queue=MONITOR_SPECS["rabbitmq"]["queue"],
    )


async def _run_queue_action(
    target_name: str,
    payload: RabbitQueueActionRequest,
    action: str,
    request: Request,
    current_user: dict,
) -> RabbitQueueActionResponse:
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Конфигурация пока не загружена.",
        )
    if not any(target.name == target_name for target in current_config.rabbitmq_targets):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Монитор RabbitMQ {target_name} не найден.",
        )

    vhost = payload.vhost or "/"
    cached_metrics = _get_cached_queue_metrics(target_name, vhost, payload.name)
    try:
        result = collector.manage_queue(target_name, vhost, payload.name, action)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Операция мониторинга не выполнена. Проверьте доступность сервиса и права учетной записи.")
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Учетная запись RabbitMQ не разрешает это действие. Права на сервере автоматически не расширяются.")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Операция мониторинга не выполнена. Проверьте доступность сервиса и права учетной записи.")
    except Exception as exc:
        logger.error("RabbitMQ queue %s failed for target %s queue %s/%s: %s", action, target_name, vhost, payload.name, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Операция мониторинга не выполнена. Проверьте доступность сервиса и права учетной записи.")

    refresh_task = _queue_refresh(target_name)
    audit_log(
        f"rabbitmq_queue_{action}",
        user=current_user,
        request=request,
        permission="rabbitmq.messages.purge" if action == "purge" else "rabbitmq.queues.delete",
        target_type="rabbitmq",
        target_name=target_name,
        vhost=vhost,
        queue=payload.name,
        refresh_task_id=refresh_task.id,
        **cached_metrics,
    )
    return RabbitQueueActionResponse(
        status="queued_refresh",
        action=result["action"],
        target_name=target_name,
        vhost=vhost,
        queue=payload.name,
        refresh_task_id=refresh_task.id,
    )


@router.post("/{target_name}/queues/purge", response_model=RabbitQueueActionResponse)
async def purge_rabbitmq_queue(
    target_name: str,
    payload: RabbitQueueActionRequest,
    request: Request,
    current_user: dict = Depends(require_permission("rabbitmq.messages.purge")),
):
    """Purge all messages from a RabbitMQ queue."""
    return await _run_queue_action(target_name, payload, "purge", request, current_user)


@router.post("/{target_name}/queues/delete", response_model=RabbitQueueActionResponse)
async def delete_rabbitmq_queue(
    target_name: str,
    payload: RabbitQueueActionRequest,
    request: Request,
    current_user: dict = Depends(require_permission("rabbitmq.queues.delete")),
):
    """Delete a RabbitMQ queue."""
    return await _run_queue_action(target_name, payload, "delete", request, current_user)
