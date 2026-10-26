"""
API endpoints for HAProxy monitoring data.
"""
import asyncio
import json
import logging
import os
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as UrlRequest
from app.services.http_client import open_http as urlopen

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.api.auth import require_permission
from app.audit import audit_log
from app.config import get_app_config, settings
from app.models.haproxy import HAProxyCluster
from app.services.cache import CacheService
from app.services.kubernetes_operations import evaluate_kubernetes_operation
from app.services.kubernetes_pod_resources import enrich_pods_with_resources
from app.services.operations_tracker import OperationsTracker
from app.services.sse_manager import sse_manager

HAPROXY_TARGET_CACHE_TTL_S = 60
HAPROXY_K8S_PODS_REFRESH_INTERVAL_S = 5.0
HAPROXY_K8S_PODS_FAST_REFRESH_INTERVAL_S = 1.5

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/haproxy", tags=["haproxy"])

# Initialize cache for HAProxy data
haproxy_cache = CacheService(
    db=settings.redis_haproxy_db if settings.redis_haproxy_db is not None else settings.redis_db
)
haproxy_operations_tracker = OperationsTracker(
    ttl_seconds=2592000,     # Default (30 days)
    ttl_seconds_long=2592000,   # Last 3 operations: persist 30 days (essentially forever)
    ttl_seconds_short=300,   # Older operations: delete after 5 minutes
    keep_last_n=3,
    redis_client=haproxy_cache.redis_client,
)

_k8s_pods_cache_lock = threading.Lock()
_k8s_pods_cache: Dict[str, List[Dict[str, Any]]] = {}
_k8s_pods_cache_signatures: Dict[str, str] = {}
_k8s_pods_cache_updated_at: Dict[str, float] = {}


class HAProxyOperationTimelineEvent(BaseModel):
    status: str
    progress: str
    message: str
    timestamp: str


class HAProxyOperationResponse(BaseModel):
    operation_id: str
    status: str
    message: str
    target_name: str
    pod_name: Optional[str] = None
    deployment_name: Optional[str] = None


class HAProxyOperationStatusResponse(BaseModel):
    operation_id: str
    type: str
    status: str
    progress: str
    message: str
    target_name: str
    pod_name: Optional[str] = None
    deployment_name: Optional[str] = None
    replacement_pod_name: Optional[str] = None
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None
    timeline: List[HAProxyOperationTimelineEvent]


def _publish_operation_event(operation: Optional[Dict[str, Any]]) -> None:
    if not operation:
        return
    details = operation.get("details") or {}
    sse_manager.publish_background(
        {
            "event": "operations.updated",
            "domain": "kubernetes.operations",
            "target_type": "haproxy",
            "target_name": operation.get("target_name"),
            "operation_id": operation.get("id"),
            "operation_type": operation.get("type"),
            "status": operation.get("status"),
            "progress": operation.get("progress"),
            "message": operation.get("message"),
            "pod_name": details.get("pod_name"),
            "deployment_name": details.get("deployment_name"),
            "updated_at": operation.get("updated_at"),
        }
    )


def _create_operation(
    *,
    operation_type: str,
    target_name: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    operation = haproxy_operations_tracker.create(
        operation_type=operation_type,
        target_name=target_name,
        message=message,
        details=details,
    )
    _publish_operation_event(operation)
    return operation


def _append_operation_event(
    operation_id: str,
    *,
    status: str,
    progress: str,
    message: str,
    details_patch: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    operation = haproxy_operations_tracker.append_event(
        operation_id,
        status=status,
        progress=progress,
        message=message,
        details_patch=details_patch,
    )
    _publish_operation_event(operation)
    return operation


def _target_options_or_404(target_name: str) -> Dict[str, Any]:
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuration is not initialized yet",
        )

    target = current_config.get_haproxy_target_by_name(target_name)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HAProxy target {target_name} not found",
        )
    return target.options or {}


def _k8s_context_or_400(options: Dict[str, Any]) -> Dict[str, str]:
    k8s_config = options.get("kubernetes") or {}
    if not k8s_config.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HAProxy restart operations require options.kubernetes.enabled=true",
        )

    namespace = str(k8s_config.get("namespace") or "").strip()
    service_name = str(k8s_config.get("serviceName") or "").strip()
    if not namespace or not service_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="kubernetes.namespace and kubernetes.serviceName are required",
        )

    deployment_name = str(k8s_config.get("deploymentName") or "").strip()
    return {
        "namespace": namespace,
        "service_name": service_name,
        "deployment_name": deployment_name,
    }


def _incluster_api_context_or_503() -> Dict[str, str]:
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    api_host = os.environ.get("KUBERNETES_SERVICE_HOST")
    api_port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not api_host:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="KUBERNETES_SERVICE_HOST is not set",
        )
    if not os.path.exists(token_path) or not os.path.exists(ca_path):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service account token/cert files are not available",
        )

    try:
        with open(token_path, "r", encoding="utf-8") as f:
            token = f.read().strip()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not read service account token: {e}",
        ) from e

    return {
        "base": f"https://{api_host}:{api_port}",
        "token": token,
        "ca_path": ca_path,
    }


def _k8s_request_json(
    method: str,
    url: str,
    token: str,
    ca_path: str,
    timeout_s: float = 5.0,
    body: Optional[Dict[str, Any]] = None,
) -> Any:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = UrlRequest(url, data=data, method=method.upper())
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/merge-patch+json")

    ssl_context = ssl.create_default_context(cafile=ca_path)
    try:
        with urlopen(req, timeout=timeout_s, context=ssl_context) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        raw = e.read().decode("utf-8") if hasattr(e, "read") else ""
        detail = raw or str(e)
        raise HTTPException(status_code=e.code, detail=f"Kubernetes API error: {detail}") from e
    except URLError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Kubernetes API is unreachable: {e}",
        ) from e


def _discover_target_pod_names(
    base: str,
    token: str,
    ca_path: str,
    namespace: str,
    service_name: str,
    timeout_s: float = 5.0,
) -> Set[str]:
    pod_names: Set[str] = set()
    selector = quote(f"kubernetes.io/service-name={service_name}", safe="")
    eps_url = (
        f"{base}/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices"
        f"?labelSelector={selector}"
    )
    try:
        payload = _k8s_request_json("GET", eps_url, token, ca_path, timeout_s=timeout_s)
        for item in payload.get("items", []):
            for endpoint in item.get("endpoints", []):
                pod_name = ((endpoint.get("targetRef") or {}).get("name"))
                if pod_name:
                    pod_names.add(pod_name)
    except HTTPException:
        # Fallback to Endpoints below.
        pass

    if pod_names:
        return pod_names

    endpoints_url = f"{base}/api/v1/namespaces/{namespace}/endpoints/{service_name}"
    payload = _k8s_request_json("GET", endpoints_url, token, ca_path, timeout_s=timeout_s)
    for subset in payload.get("subsets", []):
        for addr in subset.get("addresses", []):
            pod_name = ((addr.get("targetRef") or {}).get("name")) or addr.get("hostname")
            if pod_name:
                pod_names.add(pod_name)
    return pod_names


def _resolve_deployment_name_for_pod(
    base: str,
    token: str,
    ca_path: str,
    namespace: str,
    pod_name: str,
    timeout_s: float = 5.0,
) -> Optional[str]:
    pod_url = f"{base}/api/v1/namespaces/{namespace}/pods/{pod_name}"
    pod_payload = _k8s_request_json("GET", pod_url, token, ca_path, timeout_s=timeout_s)
    owners = ((pod_payload.get("metadata") or {}).get("ownerReferences") or [])
    rs_name = None
    for owner in owners:
        if owner.get("kind") == "ReplicaSet":
            rs_name = owner.get("name")
            break
    if not rs_name:
        return None

    rs_url = f"{base}/apis/apps/v1/namespaces/{namespace}/replicasets/{rs_name}"
    rs_payload = _k8s_request_json("GET", rs_url, token, ca_path, timeout_s=timeout_s)
    rs_owners = ((rs_payload.get("metadata") or {}).get("ownerReferences") or [])
    for owner in rs_owners:
        if owner.get("kind") == "Deployment":
            return owner.get("name")
    return None


def _is_pod_ready(pod: Dict[str, Any]) -> bool:
    status = pod.get("status") or {}
    conditions = status.get("conditions") or []
    for condition in conditions:
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    container_statuses = status.get("containerStatuses") or []
    if container_statuses:
        return all(bool(c.get("ready")) for c in container_statuses)
    return False


def _pod_items_from_pods_payload(
    pods_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build normalized pod items from K8s list pods response."""
    items = []
    for pod in pods_payload.get("items", []):
        metadata = pod.get("metadata") or {}
        status = pod.get("status") or {}
        container_statuses = status.get("containerStatuses") or []
        restart_sum = sum(int(c.get("restartCount") or 0) for c in container_statuses)
        deletion_ts = metadata.get("deletionTimestamp")
        terminating = bool(deletion_ts)
        owners = metadata.get("ownerReferences") or []
        owner_kind = None
        owner_name = None
        for owner in owners:
            if owner.get("controller") is True:
                owner_kind = owner.get("kind")
                owner_name = owner.get("name")
                break
        if owner_name is None and owners:
            owner_kind = owners[0].get("kind")
            owner_name = owners[0].get("name")

        phase = status.get("phase")
        detailed_status = phase
        reason = status.get("reason")
        if reason:
            detailed_status = reason

        if container_statuses:
            for c in container_statuses:
                state = c.get("state") or {}
                waiting = state.get("waiting")
                terminated = state.get("terminated")
                if terminated and terminated.get("reason"):
                    detailed_status = terminated.get("reason")
                    break
                if waiting and waiting.get("reason"):
                    detailed_status = waiting.get("reason")
                    break

        if terminating:
            detailed_status = "Terminating"

        items.append(
            {
                "name": metadata.get("name"),
                "uid": metadata.get("uid"),
                "ready": _is_pod_ready(pod),
                "phase": phase,
                "status_detail": detailed_status,
                "terminating": terminating,
                "restarts": restart_sum,
                "start_time": status.get("startTime"),
                "owner_kind": owner_kind,
                "owner_name": owner_name,
            }
        )
    return items


def _list_service_pods(
    base: str,
    token: str,
    ca_path: str,
    namespace: str,
    service_name: str,
    timeout_s: float = 5.0,
) -> List[Dict[str, Any]]:
    """List pods matching the Service selector."""
    service_url = f"{base}/api/v1/namespaces/{namespace}/services/{service_name}"
    service_payload = _k8s_request_json("GET", service_url, token, ca_path, timeout_s=timeout_s)
    selector_map = ((service_payload.get("spec") or {}).get("selector")) or {}
    if not selector_map:
        return []
    selector = ",".join(f"{k}={v}" for k, v in selector_map.items())
    encoded_selector = quote(selector, safe="")
    pods_url = f"{base}/api/v1/namespaces/{namespace}/pods?labelSelector={encoded_selector}"
    pods_payload = _k8s_request_json("GET", pods_url, token, ca_path, timeout_s=timeout_s)
    return _pod_items_from_pods_payload(pods_payload)


def _list_deployment_pods(
    base: str,
    token: str,
    ca_path: str,
    namespace: str,
    deployment_name: str,
    timeout_s: float = 5.0,
) -> List[Dict[str, Any]]:
    """List pods belonging to the Deployment (by spec.selector.matchLabels)."""
    dep_url = f"{base}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}"
    dep_payload = _k8s_request_json("GET", dep_url, token, ca_path, timeout_s=timeout_s)
    selector = (dep_payload.get("spec") or {}).get("selector") or {}
    match_labels = selector.get("matchLabels")
    if not match_labels or not isinstance(match_labels, dict):
        return []
    selector_str = ",".join(f"{k}={v}" for k, v in match_labels.items())
    encoded = quote(selector_str, safe="")
    pods_url = f"{base}/api/v1/namespaces/{namespace}/pods?labelSelector={encoded}"
    pods_payload = _k8s_request_json("GET", pods_url, token, ca_path, timeout_s=timeout_s)
    return _pod_items_from_pods_payload(pods_payload)


def _compact_k8s_pods(raw_pods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pods = []
    for p in raw_pods:
        state = {
            "name": p.get("name"),
            "uid": p.get("uid"),
            "phase": p.get("phase"),
            "status_detail": p.get("status_detail"),
            "ready": bool(p.get("ready")),
            "terminating": bool(p.get("terminating")),
            "restarts": p.get("restarts"),
            "start_time": p.get("start_time"),
        }
        if p.get("resources"):
            state["resources"] = p.get("resources")
        pods.append(state)
    return pods


def _target_k8s_context(target_name: str) -> Optional[Dict[str, str]]:
    config = get_app_config()
    if not config:
        return None
    target = config.get_haproxy_target_by_name(target_name)
    options = (target.options if target else {}) or {}
    k8s_cfg = options.get("kubernetes") or {}
    if not k8s_cfg.get("enabled"):
        return None
    namespace = str(k8s_cfg.get("namespace") or target_name).strip()
    service_name = str(k8s_cfg.get("serviceName") or target_name).strip()
    deployment_name = str(k8s_cfg.get("deploymentName") or "").strip()
    if not namespace:
        return None
    return {
        "namespace": namespace,
        "service_name": service_name,
        "deployment_name": deployment_name,
    }


def _fetch_target_k8s_pods(target_name: str) -> List[Dict[str, Any]]:
    target_k8s = _target_k8s_context(target_name)
    if not target_k8s:
        return []
    kctx = _incluster_api_context_or_503()
    raw_pods: List[Dict[str, Any]] = []
    if target_k8s["deployment_name"]:
        raw_pods = _list_deployment_pods(
            kctx["base"],
            kctx["token"],
            kctx["ca_path"],
            target_k8s["namespace"],
            target_k8s["deployment_name"],
        )
    if not raw_pods and target_k8s["service_name"]:
        raw_pods = _list_service_pods(
            kctx["base"],
            kctx["token"],
            kctx["ca_path"],
            target_k8s["namespace"],
            target_k8s["service_name"],
        )
    pods = _compact_k8s_pods(raw_pods)
    return enrich_pods_with_resources(target_k8s["namespace"], pods)


def _get_cached_k8s_pods(target_name: str) -> Optional[List[Dict[str, Any]]]:
    with _k8s_pods_cache_lock:
        pods = _k8s_pods_cache.get(target_name)
        if pods is None:
            return None
        return [dict(pod) for pod in pods]


def _store_k8s_pods_snapshot(target_name: str, pods: List[Dict[str, Any]]) -> bool:
    signature = json.dumps(pods, sort_keys=True, default=str)
    with _k8s_pods_cache_lock:
        changed = _k8s_pods_cache_signatures.get(target_name) != signature
        _k8s_pods_cache[target_name] = [dict(pod) for pod in pods]
        _k8s_pods_cache_signatures[target_name] = signature
        _k8s_pods_cache_updated_at[target_name] = time.monotonic()
        return changed


async def refresh_haproxy_k8s_pods_snapshot(
    target_name: str,
    *,
    publish_changed: bool = True,
) -> bool:
    """Refresh cached K8s pod snapshot for one HAProxy target without blocking GET requests."""
    try:
        pods = await asyncio.to_thread(_fetch_target_k8s_pods, target_name)
    except HTTPException as e:
        logger.debug("Refresh K8s pod snapshot for %s failed: %s", target_name, e.detail)
        return False
    except Exception as e:
        logger.warning("Refresh K8s pod snapshot for %s failed: %s", target_name, e)
        return False

    changed = _store_k8s_pods_snapshot(target_name, pods)
    if changed and publish_changed:
        await sse_manager.publish(
            {
                "event": "haproxy.k8s.changed",
                "target_type": "haproxy",
                "target_name": target_name,
                "pods_state": pods,
            },
            fanout=True,
        )
    return changed


def _target_has_running_operation(target_name: str) -> bool:
    try:
        return any(
            op.get("status") in ("queued", "running")
            for op in haproxy_operations_tracker.list_recent(target_name, limit=20)
        )
    except Exception:
        return False


async def refresh_haproxy_k8s_pods_snapshots() -> bool:
    """Refresh all HAProxy K8s pod snapshots. Returns True when fast polling is needed."""
    target_names = _get_k8s_enabled_target_names()
    if not target_names:
        return False
    await asyncio.gather(
        *(refresh_haproxy_k8s_pods_snapshot(name) for name in target_names),
        return_exceptions=True,
    )
    return any(_target_has_running_operation(name) for name in target_names)


def _operation_to_response(operation: Dict[str, Any]) -> HAProxyOperationStatusResponse:
    details = operation.get("details") or {}
    timeline_raw = operation.get("timeline") or []
    timeline = [
        HAProxyOperationTimelineEvent(
            status=str(item.get("status") or ""),
            progress=str(item.get("progress") or ""),
            message=str(item.get("message") or ""),
            timestamp=str(item.get("timestamp") or ""),
        )
        for item in timeline_raw
    ]
    replacement_pod_names = details.get("replacement_pod_names") or []
    replacement_pod_name = replacement_pod_names[0] if replacement_pod_names else None
    if replacement_pod_name is not None:
        replacement_pod_name = str(replacement_pod_name)

    return HAProxyOperationStatusResponse(
        operation_id=str(operation.get("id") or ""),
        type=str(operation.get("type") or ""),
        status=str(operation.get("status") or ""),
        progress=str(operation.get("progress") or ""),
        message=str(operation.get("message") or ""),
        target_name=str(operation.get("target_name") or ""),
        pod_name=(str(details.get("pod_name")) if details.get("pod_name") else None),
        deployment_name=(str(details.get("deployment_name")) if details.get("deployment_name") else None),
        replacement_pod_name=replacement_pod_name,
        created_at=str(operation.get("created_at") or ""),
        updated_at=str(operation.get("updated_at") or ""),
        completed_at=(str(operation.get("completed_at")) if operation.get("completed_at") else None),
        timeline=timeline,
    )


def _refresh_target_cache_sync(target_name: str) -> None:
    """Refresh HAProxy target cache (run in executor to avoid blocking)."""
    try:
        from app.services.haproxy_collector import HAProxyCollector

        collector = HAProxyCollector()
        data = collector.collect_target_data(target_name)
        if data:
            cache_key = f"haproxy:target:{target_name}"
            haproxy_cache.redis_client.setex(
                cache_key,
                HAPROXY_TARGET_CACHE_TTL_S,
                json.dumps(data, default=str),
            )
            logger.debug("Refreshed HAProxy target cache after pod restart: %s", target_name)
    except Exception as e:
        logger.warning("Failed to refresh HAProxy target cache for %s: %s", target_name, e)


def _refresh_target_cache_after_pod_restart(target_name: str) -> None:
    """Schedule a background refresh of target cache so UI sees new pod list promptly."""
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _refresh_target_cache_sync, target_name)


def _refresh_operation_status(operation: Dict[str, Any], operation_id: str) -> Dict[str, Any]:
    if operation.get("status") in {"succeeded", "failed"}:
        return operation
    try:
        kctx = _incluster_api_context_or_503()
        return _evaluate_haproxy_operation(operation, kctx)
    except HTTPException as e:
        return _append_operation_event(
            operation_id,
            status="failed",
            progress="kubernetes_error",
            message=f"Could not evaluate operation status: {e.detail}",
        ) or operation
    except Exception as e:
        return _append_operation_event(
            operation_id,
            status="failed",
            progress="unexpected_error",
            message=f"Unexpected error while evaluating operation status: {e}",
        ) or operation


def _evaluate_haproxy_operation(operation: Dict[str, Any], kctx: Dict[str, str]) -> Dict[str, Any]:
    return evaluate_kubernetes_operation(
        operation=operation,
        kctx=kctx,
        append_event=_append_operation_event,
        list_service_pods=lambda base, token, ca_path, namespace, service_name: _list_service_pods(
            base=base,
            token=token,
            ca_path=ca_path,
            namespace=namespace,
            service_name=service_name,
        ),
        k8s_request_json=_k8s_request_json,
    )


def _get_k8s_enabled_target_names() -> List[str]:
    """Return HAProxy target names that have Kubernetes restart enabled (for background refresh)."""
    config = get_app_config()
    if not config:
        return []
    return [
        t.name
        for t in config.haproxy_targets
        if bool(((t.options or {}).get("kubernetes") or {}).get("enabled"))
    ]


async def refresh_running_operations() -> None:
    """
    Evaluate all running/queued K8s operations and publish SSE when status changes.
    Called from a background task so UI gets updates with minimal delay without relying only on polling.
    """
    target_names = _get_k8s_enabled_target_names()
    if not target_names:
        return
    for target_name in target_names:
        try:
            operations = haproxy_operations_tracker.list_recent(target_name, limit=20)
        except Exception as e:
            logger.debug("List operations for %s failed: %s", target_name, e)
            continue
        for operation in operations:
            status = operation.get("status")
            if status not in ("queued", "running"):
                continue
            operation_id = str(operation.get("id") or "")
            if not operation_id:
                continue
            try:
                updated = await asyncio.to_thread(
                    _refresh_operation_status, operation, operation_id
                )
            except Exception as e:
                logger.debug("Refresh operation %s failed: %s", operation_id, e)
                continue
            if not updated:
                continue
            # _refresh_operation_status runs in thread so publish_background is no-op; publish from here
            if (
                updated.get("status") != operation.get("status")
                or updated.get("progress") != operation.get("progress")
            ):
                details = updated.get("details") or {}
                await sse_manager.publish(
                    {
                        "event": "operations.updated",
                        "domain": "kubernetes.operations",
                        "target_type": "haproxy",
                        "target_name": updated.get("target_name"),
                        "operation_id": updated.get("id"),
                        "operation_type": updated.get("type"),
                        "status": updated.get("status"),
                        "progress": updated.get("progress"),
                        "message": updated.get("message"),
                        "pod_name": details.get("pod_name"),
                        "deployment_name": details.get("deployment_name"),
                        "updated_at": updated.get("updated_at"),
                    },
                    fanout=True,
                )
                if updated.get("status") in ("succeeded", "failed"):
                    tname = str(updated.get("target_name") or "")
                    if updated.get("type") in ("k8s.pod_restart", "k8s.rollout_restart"):
                        _refresh_target_cache_after_pod_restart(tname)
                        await refresh_haproxy_k8s_pods_snapshot(tname)


@router.get("/{target_name}", response_model=HAProxyCluster)
async def get_haproxy_target(target_name: str):
    """Get HAProxy monitoring data for a specific target."""
    current_config = get_app_config()
    if not current_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuration is not initialized yet",
        )

    if not any(target.name == target_name for target in current_config.haproxy_targets):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HAProxy target {target_name} not found",
        )

    cache_key = f"haproxy:target:{target_name}"
    cached = haproxy_cache.redis_client.get(cache_key)
    if cached:
        payload = json.loads(cached)
        target = current_config.get_haproxy_target_by_name(target_name)
        options = (target.options if target else {}) or {}
        k8s_cfg = options.get("kubernetes") or {}
        restart_supported = bool(k8s_cfg.get("enabled"))
        k8s_pods = _get_cached_k8s_pods(target_name)
        if k8s_pods is None:
            k8s_pods = payload.get("k8s_pods")
        return HAProxyCluster(
            name=payload["name"],
            overview=payload["overview"],
            proxies=payload["proxies"],
            pods=payload.get("pods"),
            k8s_pods=k8s_pods,
            ingresses=payload.get("ingresses"),
            last_update=payload.get("last_update"),
            restart_supported=restart_supported,
        )

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="HAProxy data is warming up after restart. Values will appear automatically in a few seconds.",
    )


@router.post(
    "/{target_name}/pods/{pod_name}/restart",
    response_model=HAProxyOperationResponse,
)
async def restart_haproxy_pod(
    target_name: str,
    pod_name: str,
    request: Request,
    current_user: dict = Depends(require_permission("haproxy.restart")),
):
    options = _target_options_or_404(target_name)
    k8s = _k8s_context_or_400(options)
    kctx = _incluster_api_context_or_503()

    pod_names = _discover_target_pod_names(
        base=kctx["base"],
        token=kctx["token"],
        ca_path=kctx["ca_path"],
        namespace=k8s["namespace"],
        service_name=k8s["service_name"],
    )
    if pod_name not in pod_names:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pod {pod_name} is not part of HAProxy target {target_name}",
        )

    pod_url = f"{kctx['base']}/api/v1/namespaces/{k8s['namespace']}/pods/{pod_name}"
    pod_payload = _k8s_request_json("GET", pod_url, kctx["token"], kctx["ca_path"])
    metadata = pod_payload.get("metadata") or {}
    owners = metadata.get("ownerReferences") or []
    owner_kind = None
    owner_name = None
    for owner in owners:
        if owner.get("controller") is True:
            owner_kind = owner.get("kind")
            owner_name = owner.get("name")
            break
    if owner_name is None and owners:
        owner_kind = owners[0].get("kind")
        owner_name = owners[0].get("name")

    operation = _create_operation(
        operation_type="k8s.pod_restart",
        target_name=target_name,
        message=f"Restart command accepted for pod {pod_name}",
        details={
            "namespace": k8s["namespace"],
            "service_name": k8s["service_name"],
            "pod_name": pod_name,
            "old_pod_uid": metadata.get("uid"),
            "owner_kind": owner_kind,
            "owner_name": owner_name,
        },
    )
    operation_id = str(operation.get("id") or "")
    _k8s_request_json("DELETE", pod_url, kctx["token"], kctx["ca_path"])
    _append_operation_event(
        operation_id,
        status="running",
        progress="terminating_old_pod",
        message=f"Pod {pod_name} deletion requested",
    )
    asyncio.create_task(refresh_haproxy_k8s_pods_snapshot(target_name))
    audit_log(
        "haproxy_pod_restart",
        user=current_user,
        request=request,
        permission="haproxy.restart",
        target_type="haproxy",
        target_name=target_name,
        namespace=k8s["namespace"],
        service_name=k8s["service_name"],
        pod_name=pod_name,
        operation_id=operation_id,
        owner_kind=owner_kind,
        owner_name=owner_name,
    )

    return HAProxyOperationResponse(
        operation_id=operation_id,
        status="accepted",
        message=f"Restart for pod {pod_name} has been triggered",
        target_name=target_name,
        pod_name=pod_name,
    )


@router.post(
    "/{target_name}/rollout-restart",
    response_model=HAProxyOperationResponse,
)
async def rollout_restart_haproxy_target(
    target_name: str,
    request: Request,
    current_user: dict = Depends(require_permission("haproxy.restart")),
):
    options = _target_options_or_404(target_name)
    k8s = _k8s_context_or_400(options)
    kctx = _incluster_api_context_or_503()

    deployment_name = k8s["deployment_name"]
    if not deployment_name:
        pod_names = _discover_target_pod_names(
            base=kctx["base"],
            token=kctx["token"],
            ca_path=kctx["ca_path"],
            namespace=k8s["namespace"],
            service_name=k8s["service_name"],
        )
        if not pod_names:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No pods found for target {target_name}",
            )
        sample_pod = sorted(pod_names)[0]
        deployment_name = _resolve_deployment_name_for_pod(
            base=kctx["base"],
            token=kctx["token"],
            ca_path=kctx["ca_path"],
            namespace=k8s["namespace"],
            pod_name=sample_pod,
        )
        if not deployment_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not resolve Deployment for target pods. "
                    "Set options.kubernetes.deploymentName explicitly."
                ),
            )

    deployment_url = (
        f"{kctx['base']}/apis/apps/v1/namespaces/{k8s['namespace']}/deployments/{deployment_name}"
    )
    restarted_at = datetime.now(timezone.utc).isoformat()
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": restarted_at,
                    }
                }
            }
        }
    }
    patch_response = _k8s_request_json(
        "PATCH",
        deployment_url,
        kctx["token"],
        kctx["ca_path"],
        body=patch,
    )
    generation = ((patch_response.get("metadata") or {}).get("generation"))
    operation = _create_operation(
        operation_type="k8s.rollout_restart",
        target_name=target_name,
        message=f"Rollout restart accepted for deployment {deployment_name}",
        details={
            "namespace": k8s["namespace"],
            "service_name": k8s["service_name"],
            "deployment_name": deployment_name,
            "target_generation": generation,
            "restarted_at": restarted_at,
        },
    )
    operation_id = str(operation.get("id") or "")
    _append_operation_event(
        operation_id,
        status="running",
        progress="rollout_in_progress",
        message=f"Rollout requested for deployment {deployment_name}",
    )
    asyncio.create_task(refresh_haproxy_k8s_pods_snapshot(target_name))
    audit_log(
        "haproxy_rollout_restart",
        user=current_user,
        request=request,
        permission="haproxy.restart",
        target_type="haproxy",
        target_name=target_name,
        namespace=k8s["namespace"],
        service_name=k8s["service_name"],
        deployment_name=deployment_name,
        operation_id=operation_id,
        restarted_at=restarted_at,
    )

    return HAProxyOperationResponse(
        operation_id=operation_id,
        status="accepted",
        message=f"Rollout restart for deployment {deployment_name} has been triggered",
        target_name=target_name,
        deployment_name=deployment_name,
    )


@router.get(
    "/operations/{operation_id}",
    response_model=HAProxyOperationStatusResponse,
)
async def get_haproxy_operation_status(
    operation_id: str,
    _: dict = Depends(require_permission("haproxy.restart")),
):
    operation = haproxy_operations_tracker.get(operation_id)
    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Operation {operation_id} not found or expired",
        )

    was_succeeded = operation.get("status") == "succeeded"
    operation = _refresh_operation_status(operation, operation_id)
    if not was_succeeded and operation.get("status") == "succeeded":
        tname = str(operation.get("target_name") or "")
        if operation.get("type") in ("k8s.pod_restart", "k8s.rollout_restart"):
            _refresh_target_cache_after_pod_restart(tname)
            await refresh_haproxy_k8s_pods_snapshot(tname)

    return _operation_to_response(operation)


@router.get(
    "/{target_name}/operations",
    response_model=List[HAProxyOperationStatusResponse],
)
async def list_haproxy_operations(
    target_name: str,
    limit: int = Query(default=10, ge=1, le=50),
    _: dict = Depends(require_permission("haproxy.restart")),
):
    # Ensure target exists.
    _target_options_or_404(target_name)

    operations = haproxy_operations_tracker.list_recent(target_name, limit=limit)
    refreshed: List[HAProxyOperationStatusResponse] = []
    for operation in operations:
        operation_id = str(operation.get("id") or "")
        if not operation_id:
            continue
        was_succeeded = operation.get("status") == "succeeded"
        current = _refresh_operation_status(operation, operation_id)
        if not was_succeeded and current.get("status") == "succeeded":
            tname = str(current.get("target_name") or "")
            if current.get("type") in ("k8s.pod_restart", "k8s.rollout_restart"):
                _refresh_target_cache_after_pod_restart(tname)
                await refresh_haproxy_k8s_pods_snapshot(tname)
        refreshed.append(_operation_to_response(current))
    return refreshed
