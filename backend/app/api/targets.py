"""Generic monitoring targets API (type-driven entrypoint)."""
import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.audit import audit_log
from app.config import get_app_config, settings
from app.models.cluster import MonitoringTargetSummary, UnreachableHost
from app.services.celery_app import MONITOR_SPECS, celery_app
from app.services.cache import CacheService
from app.services.monitor_runtime import get_monitor_cache
from app.services.registry_cache import registry_cache_service

# OK statuses per target type (host/node is healthy). Anything else => unreachable banner.
# MongoDB: use node status (UP/DOWN from replica set health), not stateStr.
RABBITMQ_OK = {"running": True, "status": "RUNNING"}
REDIS_OK_STATUS = "UP"
DISK_CRITICAL_USED_PERCENT = 95.0

router = APIRouter(prefix="/api/targets", tags=["targets"])

cache = CacheService()
rabbitmq_cache = CacheService(
    db=settings.redis_rabbitmq_db if settings.redis_rabbitmq_db is not None else settings.redis_db
)
redis_cache = CacheService(
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
haproxy_cache = CacheService(
    db=settings.redis_haproxy_db if settings.redis_haproxy_db is not None else settings.redis_db
)
logging_cache = CacheService(
    db=settings.redis_logging_db if settings.redis_logging_db is not None else settings.redis_db
)
emqx_cache = CacheService(
    db=settings.redis_emqx_db if settings.redis_emqx_db is not None else settings.redis_db
)
argo_rollout_cache = CacheService(
    db=settings.redis_rollouts_db if settings.redis_rollouts_db is not None else settings.redis_db
)
SUPPORTED_TARGET_TYPES = (
    "mongodb",
    "rabbitmq",
    "redis",
    "postgresql",
    "kafka",
    "haproxy",
    "redpanda",
    "elasticsearch",
    "kibana",
    "emqx",
    "argo-rollout",
    "addresses",
    "registry",
)
LOGGING_TYPES = ("redpanda", "elasticsearch", "kibana")
REFRESH_TARGET_TYPES = (
    "mongodb",
    "rabbitmq",
    "redis",
    "postgresql",
    "kafka",
    "haproxy",
    "redpanda",
    "elasticsearch",
    "emqx",
    "argo-rollout",
    "addresses",
    "registry",
)


class TargetRefreshQueuedResponse(BaseModel):
    queued: bool = True
    target_id: str
    target_type: str
    target_name: str
    task_id: str
    status: str = Field(default="queued")


def _cache_get(cache_service: CacheService, key: str):
    return cache_service.get_raw(key)


def _build_mongodb_target_summary(cluster_name: str) -> MonitoringTargetSummary:
    cached_data = cache.get_cluster_data(cluster_name)
    last_update = None
    status_label = "warming_up"
    if cached_data:
        status_label = "ready"
        raw_last_update = cached_data.get("last_update")
        if isinstance(raw_last_update, str):
            try:
                last_update = datetime.fromisoformat(raw_last_update)
            except Exception:
                last_update = None

    return MonitoringTargetSummary(
        id=f"mongodb:{cluster_name}",
        name=cluster_name,
        type="mongodb",
        status=status_label,
        last_update=last_update,
        group=None,
    )


def _build_generic_target_summary(target_type: str, target_name: str) -> MonitoringTargetSummary:
    """Build summary for non-Mongo target types."""
    normalized_target_type = "addresses" if target_type in {"addresses", "registry"} else target_type
    status_label = "not_implemented"
    last_update = None

    if target_type == "rabbitmq":
        cached_payload = _cache_get(rabbitmq_cache, f"rabbitmq:target:{target_name}")
        status_label = "ready" if cached_payload else "configured"
        if cached_payload:
            try:
                payload = json.loads(cached_payload)
                raw_last_update = payload.get("last_update")
                if isinstance(raw_last_update, str):
                    last_update = datetime.fromisoformat(raw_last_update)
            except Exception:
                last_update = None
    elif target_type in {"postgresql", "kafka"}:
        payload = get_monitor_cache(target_type).get_json(f"{target_type}:target:{target_name}")
        status_label = payload.get("overview", {}).get("status", "configured") if payload else "configured"
        if payload and isinstance(payload.get("last_update"), str):
            last_update = datetime.fromisoformat(payload["last_update"])
    elif target_type == "redis":
        cached_payload = _cache_get(redis_cache, f"redis:target:{target_name}")
        status_label = "ready" if cached_payload else "configured"
        if cached_payload:
            try:
                payload = json.loads(cached_payload)
                raw_last_update = payload.get("last_update")
                if isinstance(raw_last_update, str):
                    last_update = datetime.fromisoformat(raw_last_update)
            except Exception:
                last_update = None
    elif target_type == "haproxy":
        cached_payload = _cache_get(haproxy_cache, f"haproxy:target:{target_name}")
        status_label = "ready" if cached_payload else "configured"
        if cached_payload:
            try:
                payload = json.loads(cached_payload)
                raw_last_update = payload.get("last_update")
                if isinstance(raw_last_update, str):
                    last_update = datetime.fromisoformat(raw_last_update)
            except Exception:
                last_update = None
    elif target_type in LOGGING_TYPES:
        cache_key = f"{target_type}:target:{target_name}"
        cached_payload = _cache_get(logging_cache, cache_key)
        status_label = "ready" if cached_payload else "configured"
        if cached_payload:
            try:
                payload = json.loads(cached_payload)
                raw_last_update = payload.get("last_update")
                if isinstance(raw_last_update, str):
                    last_update = datetime.fromisoformat(raw_last_update)
            except Exception:
                last_update = None
    elif target_type == "emqx":
        cached_payload = _cache_get(emqx_cache, f"emqx:target:{target_name}")
        status_label = "ready" if cached_payload else "configured"
        if cached_payload:
            try:
                payload = json.loads(cached_payload)
                raw_last_update = payload.get("last_update")
                if isinstance(raw_last_update, str):
                    last_update = datetime.fromisoformat(raw_last_update)
            except Exception:
                last_update = None
    elif target_type == "argo-rollout":
        # Check cache for this specific target by name
        cache_key = f"argo_rollout:target:{target_name}"
        cached_payload = _cache_get(argo_rollout_cache, cache_key)
        status_label = "ready" if cached_payload else "configured"
        if cached_payload:
            try:
                payload = json.loads(cached_payload)
                raw_last_update = payload.get("last_update")
                if isinstance(raw_last_update, str):
                    last_update = datetime.fromisoformat(raw_last_update)
            except Exception:
                last_update = None
    elif normalized_target_type == "addresses":
        cached_payload = registry_cache_service.get_summary()
        status_label = "ready" if cached_payload else "warming_up"
        if cached_payload:
            raw_last_update = cached_payload.get("last_update")
            if isinstance(raw_last_update, str):
                try:
                    last_update = datetime.fromisoformat(raw_last_update)
                except Exception:
                    last_update = None
    group = "logging" if normalized_target_type in LOGGING_TYPES else None
    return MonitoringTargetSummary(
        id=f"{normalized_target_type}:{target_name}",
        name=target_name,
        type=normalized_target_type,
        status=status_label,
        last_update=last_update,
        group=group,
    )


@router.get("", response_model=List[MonitoringTargetSummary])
async def get_targets():
    """Get list of monitoring targets across configured providers."""
    return await asyncio.to_thread(_collect_targets)


def _collect_targets() -> List[MonitoringTargetSummary]:
    current_config = get_app_config()
    if not current_config:
        return []

    targets: List[MonitoringTargetSummary] = []
    for cluster in current_config.clusters:
        targets.append(_build_mongodb_target_summary(cluster.name))

    for target_type in ("rabbitmq", "redis", "postgresql", "kafka", "haproxy", "redpanda", "elasticsearch", "kibana", "emqx", "argo-rollout", "addresses"):
        for target in current_config.get_targets_by_type(target_type):
            targets.append(_build_generic_target_summary(target_type, target.name))

    return targets


@router.get("/unreachable/list", response_model=List[UnreachableHost])
async def get_unreachable():
    """List hosts/nodes that are not in OK status (for top banner)."""
    return await asyncio.to_thread(_collect_unreachable)


@router.get("/{target_id}", response_model=MonitoringTargetSummary)
async def get_target(target_id: str):
    """Get a single monitoring target by id (format: <type>:<target_name>)."""
    return await asyncio.to_thread(_get_target_summary, target_id)


def _get_target_summary(target_id: str) -> MonitoringTargetSummary:
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Конфигурация пока не загружена.",
        )

    target_type, separator, target_name = target_id.partition(":")
    if separator != ":" or target_type not in SUPPORTED_TARGET_TYPES or not target_name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Монитор {target_id} не найден.",
        )

    if target_type == "mongodb":
        if not current_config.get_cluster_by_name(target_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Монитор {target_id} не найден.",
            )
        return _build_mongodb_target_summary(target_name)

    typed_targets = current_config.get_targets_by_type(target_type)
    if not any(target.name == target_name for target in typed_targets):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Монитор {target_id} не найден.",
        )

    return _build_generic_target_summary(target_type, target_name)


@router.post(
    "/{target_id}/refresh",
    response_model=TargetRefreshQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_target(
    target_id: str,
    request: Request,
    current_user: dict = Depends(require_permission("targets.refresh")),
):
    """Queue refresh for one configured monitoring target."""
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Конфигурация пока не загружена.",
        )

    target_type, separator, target_name = target_id.partition(":")
    if separator != ":" or target_type not in REFRESH_TARGET_TYPES or not target_name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Монитор {target_id} не найден.",
        )

    normalized_target_type = "addresses" if target_type == "registry" else target_type
    if not any(target.name == target_name for target in current_config.get_targets_by_type(normalized_target_type)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Монитор {target_id} не найден.",
        )

    spec = MONITOR_SPECS.get(normalized_target_type)
    if not spec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target {target_id} does not support refresh",
        )

    async_result = celery_app.send_task(
        "refresh_target",
        args=[normalized_target_type, target_name],
        queue=spec["queue"],
    )
    audit_log(
        "target_refresh_queued",
        user=current_user,
        request=request,
        permission="targets.refresh",
        target_type=normalized_target_type,
        target_name=target_name,
        task_id=async_result.id,
    )
    return TargetRefreshQueuedResponse(
        target_id=f"{normalized_target_type}:{target_name}",
        target_type=normalized_target_type,
        target_name=target_name,
        task_id=async_result.id,
    )


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _append_critical_disk_usage(
    result: List[UnreachableHost],
    target_type: str,
    target_name: str,
    nodes: List[Dict[str, Any]],
) -> None:
    """Add banner entries for node exporter filesystems above the critical threshold."""
    for node in nodes:
        metrics = node.get("node_exporter") or {}
        if not isinstance(metrics, dict):
            continue

        critical_disks: List[Dict[str, Any]] = []
        for disk in metrics.get("disks") or []:
            if not isinstance(disk, dict):
                continue
            used_percent = _safe_float(disk.get("used_percent"))
            if used_percent is not None and used_percent >= DISK_CRITICAL_USED_PERCENT:
                critical_disks.append({**disk, "used_percent": used_percent})
        if not critical_disks:
            continue

        disk = max(critical_disks, key=lambda item: item["used_percent"])
        host = str(node.get("host") or node.get("name") or "?")
        mountpoint = str(disk.get("mountpoint") or "").strip()
        device = str(disk.get("device") or "").strip()
        label = host
        if mountpoint:
            label = f"{label} {mountpoint}"
        if device:
            label = f"{label} ({device})"

        result.append(
            UnreachableHost(
                host=label,
                status=f"DISK {disk['used_percent']:.0f}%",
                target_name=target_name,
                target_type=target_type,
            )
        )


def _collect_unreachable() -> List[UnreachableHost]:
    """Collect all hosts/nodes that are not in OK status from caches."""
    result: List[UnreachableHost] = []
    current_config = get_app_config()
    if not current_config:
        return result

    # MongoDB: DOWN and startup states are not OK. STANDALONE is OK for non-replicated MongoDB.
    for cluster in current_config.clusters:
        data = cache.get_cluster_data(cluster.name)
        if not data:
            result.append(
                UnreachableHost(
                    host=cluster.name,
                    status="no_data",
                    target_name=cluster.name,
                    target_type="mongodb",
                )
            )
            continue
        nodes = data.get("nodes") or []
        for node in nodes:
            node_status = (node.get("status") or "UP").strip().upper()
            state_str = (node.get("stateStr") or "").strip().upper()
            name = node.get("name") or "?"
            if node_status == "DOWN":
                result.append(
                    UnreachableHost(host=name, status="DOWN", target_name=cluster.name, target_type="mongodb")
                )
            elif state_str in ("STARTUP", "STARTUP2"):
                result.append(
                    UnreachableHost(host=name, status=state_str, target_name=cluster.name, target_type="mongodb")
                )
        _append_critical_disk_usage(result, "mongodb", cluster.name, nodes)

    # RabbitMQ: nodes not running or status != RUNNING
    for target in current_config.rabbitmq_targets:
        raw = _cache_get(rabbitmq_cache, f"rabbitmq:target:{target.name}")
        if not raw:
            result.append(
                UnreachableHost(host=target.name, status="no_data", target_name=target.name, target_type="rabbitmq")
            )
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        for node in payload.get("nodes") or []:
            running = node.get("running", False)
            st = (node.get("status") or "").strip().upper()
            name = node.get("name") or "?"
            if not running or st != "RUNNING":
                result.append(
                    UnreachableHost(
                        host=name,
                        status=st or ("running" if running else "not_running"),
                        target_name=target.name,
                        target_type="rabbitmq",
                    )
                )
        _append_critical_disk_usage(result, "rabbitmq", target.name, payload.get("nodes") or [])

    # Redis: nodes with status != UP
    for target in current_config.kafka_targets:
        payload=get_monitor_cache("kafka").get_json(f"kafka:target:{target.name}")
        status=(payload or {}).get("overview",{}).get("status","no_data")
        if status!="UP":
            result.append(UnreachableHost(host=target.name,status=status,target_name=target.name,target_type="kafka"))

    for target in current_config.postgresql_targets:
        payload = get_monitor_cache("postgresql").get_json(f"postgresql:target:{target.name}")
        if not payload:
            result.append(UnreachableHost(host=target.name, status="no_data", target_name=target.name, target_type="postgresql"))
            continue
        for node in payload.get("nodes", []):
            if node.get("status") != "UP":
                result.append(UnreachableHost(host=node.get("name", target.name), status="DOWN", target_name=target.name, target_type="postgresql"))

    for target in current_config.redis_targets:
        raw = _cache_get(redis_cache, f"redis:target:{target.name}")
        if not raw:
            result.append(
                UnreachableHost(host=target.name, status="no_data", target_name=target.name, target_type="redis")
            )
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        for node in payload.get("nodes") or []:
            st = (node.get("status") or "").strip().upper()
            name = node.get("host") or node.get("name") or "?"
            if st != REDIS_OK_STATUS:
                result.append(
                    UnreachableHost(host=name, status=st or "unknown", target_name=target.name, target_type="redis")
                )
        _append_critical_disk_usage(result, "redis", target.name, payload.get("nodes") or [])

    # HAProxy: pod-level availability only.
    # Do not report backend server statuses (e.g. "MAINT (resolution)") in unreachable banner.
    for target in current_config.haproxy_targets:
        raw = _cache_get(haproxy_cache, f"haproxy:target:{target.name}")
        if not raw:
            result.append(
                UnreachableHost(host=target.name, status="no_data", target_name=target.name, target_type="haproxy")
            )
            continue

    # Redpanda: brokers with status != UP (status set by collector from _fetch_public_metrics)
    REDPANDA_OK_STATUS = "UP"
    for target in current_config.redpanda_targets:
        raw = _cache_get(logging_cache, f"redpanda:target:{target.name}")
        if not raw:
            result.append(
                UnreachableHost(host=target.name, status="no_data", target_name=target.name, target_type="redpanda")
            )
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        for broker in payload.get("brokers") or []:
            st = (broker.get("status") or "").strip().upper()
            name = broker.get("host") or "?"
            if st != REDPANDA_OK_STATUS:
                result.append(
                    UnreachableHost(
                        host=name,
                        status=st or "unknown",
                        target_name=target.name,
                        target_type="redpanda",
                    )
                )
        _append_critical_disk_usage(result, "redpanda", target.name, payload.get("brokers") or [])
    for target in current_config.elasticsearch_targets:
        raw = _cache_get(logging_cache, f"elasticsearch:target:{target.name}")
        if not raw:
            result.append(
                UnreachableHost(
                    host=target.name, status="no_data", target_name=target.name, target_type="elasticsearch"
                )
            )
        else:
            try:
                payload = json.loads(raw)
                overview = payload.get("overview") or {}
                st = (overview.get("status") or "").strip().lower()
                if st not in ("green", "yellow"):
                    result.append(
                        UnreachableHost(
                            host=target.name,
                            status=st or "unknown",
                            target_name=target.name,
                            target_type="elasticsearch",
                        )
                    )
                _append_critical_disk_usage(result, "elasticsearch", target.name, payload.get("nodes") or [])
            except Exception:
                pass

    # EMQX: target overview and node statuses from Prometheus/API.
    EMQX_OK_STATUSES = {"UP", "RUNNING", "OK"}
    for target in current_config.emqx_targets:
        raw = _cache_get(emqx_cache, f"emqx:target:{target.name}")
        if not raw:
            result.append(
                UnreachableHost(host=target.name, status="no_data", target_name=target.name, target_type="emqx")
            )
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        overview = payload.get("overview") or {}
        overview_status = str(overview.get("status") or "").strip().upper()
        has_target_no_data = overview_status == "NO_DATA"
        if overview_status and overview_status not in EMQX_OK_STATUSES:
            result.append(
                UnreachableHost(
                    host=target.name,
                    status=overview_status,
                    target_name=target.name,
                    target_type="emqx",
                )
            )
        if has_target_no_data:
            continue
        for node in payload.get("nodes") or []:
            st = str(node.get("status") or "").strip().upper()
            name = node.get("host") or node.get("name") or "?"
            if st and st not in EMQX_OK_STATUSES:
                result.append(
                    UnreachableHost(host=name, status=st, target_name=target.name, target_type="emqx")
                )

    return result
