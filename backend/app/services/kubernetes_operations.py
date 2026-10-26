"""
Common Kubernetes operations evaluation registry.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


AppendEventFn = Callable[..., Optional[Dict[str, Any]]]
ListServicePodsFn = Callable[[str, str, str, str, str], List[Dict[str, Any]]]
K8sRequestJsonFn = Callable[..., Any]


def _evaluate_pod_restart(
    operation: Dict[str, Any],
    kctx: Dict[str, str],
    append_event: AppendEventFn,
    list_service_pods: ListServicePodsFn,
) -> Dict[str, Any]:
    details = operation.get("details") or {}
    operation_id = str(operation.get("id") or "")
    namespace = str(details.get("namespace") or "").strip()
    service_name = str(details.get("service_name") or "").strip()
    old_pod_name = str(details.get("pod_name") or "").strip()
    old_pod_uid = str(details.get("old_pod_uid") or "").strip()
    owner_name = str(details.get("owner_name") or "").strip()
    owner_kind = str(details.get("owner_kind") or "").strip()

    pods = list_service_pods(
        kctx["base"],
        kctx["token"],
        kctx["ca_path"],
        namespace,
        service_name,
    )

    old_present = any(
        (old_pod_uid and str(p.get("uid") or "") == old_pod_uid)
        or (old_pod_name and str(p.get("name") or "") == old_pod_name)
        for p in pods
    )
    replacement_candidates = [
        p
        for p in pods
        if (not old_pod_uid or str(p.get("uid") or "") != old_pod_uid)
        and (not old_pod_name or str(p.get("name") or "") != old_pod_name)
        and (
            not owner_name
            or (
                str(p.get("owner_name") or "") == owner_name
                and str(p.get("owner_kind") or "") == owner_kind
            )
        )
    ]
    replacement_ready = any(bool(p.get("ready")) for p in replacement_candidates)
    replacement_pod_names = [str(p.get("name") or "") for p in replacement_candidates if p.get("ready")]

    if old_present:
        updated = append_event(
            operation_id,
            status="running",
            progress="terminating_old_pod",
            message=f"Pod {old_pod_name} is terminating",
            details_patch={"pods_observed": len(pods)},
        )
        return updated or operation

    if replacement_ready:
        updated = append_event(
            operation_id,
            status="succeeded",
            progress="completed",
            message="Replacement pod is Ready",
            details_patch={
                "pods_observed": len(pods),
                "replacement_pod_names": replacement_pod_names,
            },
        )
        return updated or operation

    if replacement_candidates:
        updated = append_event(
            operation_id,
            status="running",
            progress="waiting_replacement_ready",
            message="Replacement pod started, waiting for Ready",
            details_patch={"pods_observed": len(pods)},
        )
        return updated or operation

    updated = append_event(
        operation_id,
        status="running",
        progress="waiting_replacement_creation",
        message="Waiting for replacement pod creation",
        details_patch={"pods_observed": len(pods)},
    )
    return updated or operation


def _evaluate_rollout_restart(
    operation: Dict[str, Any],
    kctx: Dict[str, str],
    append_event: AppendEventFn,
    k8s_request_json: K8sRequestJsonFn,
) -> Dict[str, Any]:
    details = operation.get("details") or {}
    operation_id = str(operation.get("id") or "")
    namespace = str(details.get("namespace") or "").strip()
    deployment_name = str(details.get("deployment_name") or "").strip()
    if not deployment_name:
        updated = append_event(
            operation_id,
            status="failed",
            progress="invalid_state",
            message="Operation is missing deployment name",
        )
        return updated or operation

    deployment_url = (
        f"{kctx['base']}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}"
    )
    payload = k8s_request_json(
        "GET",
        deployment_url,
        kctx["token"],
        kctx["ca_path"],
    )
    spec = payload.get("spec") or {}
    status_payload = payload.get("status") or {}
    desired = int(spec.get("replicas") or 1)
    updated_replicas = int(status_payload.get("updatedReplicas") or 0)
    ready_replicas = int(status_payload.get("readyReplicas") or 0)
    available_replicas = int(status_payload.get("availableReplicas") or 0)

    if (
        updated_replicas >= desired
        and ready_replicas >= desired
        and available_replicas >= desired
    ):
        updated = append_event(
            operation_id,
            status="succeeded",
            progress="completed",
            message=f"Rollout completed ({available_replicas}/{desired} available)",
        )
        return updated or operation

    updated = append_event(
        operation_id,
        status="running",
        progress="rollout_in_progress",
        message=(
            "Rollout in progress "
            f"(updated {updated_replicas}/{desired}, ready {ready_replicas}/{desired}, "
            f"available {available_replicas}/{desired})"
        ),
        details_patch={
            "desired_replicas": desired,
            "updated_replicas": updated_replicas,
            "ready_replicas": ready_replicas,
            "available_replicas": available_replicas,
        },
    )
    return updated or operation


def evaluate_kubernetes_operation(
    operation: Dict[str, Any],
    kctx: Dict[str, str],
    append_event: AppendEventFn,
    list_service_pods: ListServicePodsFn,
    k8s_request_json: K8sRequestJsonFn,
) -> Dict[str, Any]:
    operation_id = str(operation.get("id") or "")
    operation_type = str(operation.get("type") or "")
    details = operation.get("details") or {}

    if operation.get("status") in {"succeeded", "failed"}:
        return operation

    namespace = str(details.get("namespace") or "").strip()
    service_name = str(details.get("service_name") or "").strip()
    if not namespace or not service_name:
        updated = append_event(
            operation_id,
            status="failed",
            progress="invalid_state",
            message="Operation is missing Kubernetes namespace/service metadata",
        )
        return updated or operation

    handlers: Dict[str, Callable[[], Dict[str, Any]]] = {
        "k8s.pod_restart": lambda: _evaluate_pod_restart(
            operation,
            kctx,
            append_event,
            list_service_pods,
        ),
        "k8s.rollout_restart": lambda: _evaluate_rollout_restart(
            operation,
            kctx,
            append_event,
            k8s_request_json,
        ),
    }

    handler = handlers.get(operation_type)
    if not handler:
        updated = append_event(
            operation_id,
            status="failed",
            progress="unsupported_operation",
            message=f"Unsupported operation type: {operation_type}",
        )
        return updated or operation
    return handler()
