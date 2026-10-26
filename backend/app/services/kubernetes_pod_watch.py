from __future__ import annotations

import json
import os
import ssl
import threading
import time
from dataclasses import dataclass
from queue import Queue
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from urllib.request import Request
from app.services.http_client import open_http as urlopen

WATCH_READ_TIMEOUT_S = 4
WATCH_RESYNC_INTERVAL_S = 45


@dataclass(frozen=True)
class KubernetesApiContext:
    base: str
    token: str
    ca_path: str


@dataclass(frozen=True)
class PodWatchTarget:
    key: str
    namespace: str
    label_selector: str


def build_incluster_api_context() -> Optional[KubernetesApiContext]:
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    api_host = os.environ.get("KUBERNETES_SERVICE_HOST")
    api_port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not api_host:
        return None
    if not os.path.exists(token_path) or not os.path.exists(ca_path):
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
    except Exception:
        return None
    if not token:
        return None
    return KubernetesApiContext(
        base=f"https://{api_host}:{api_port}",
        token=token,
        ca_path=ca_path,
    )


def k8s_get_json(
    ctx: KubernetesApiContext,
    path: str,
    *,
    timeout_s: float = 5.0,
) -> Any:
    request = Request(f"{ctx.base}{path}", method="GET")
    request.add_header("Authorization", f"Bearer {ctx.token}")
    request.add_header("Accept", "application/json")
    ssl_context = ssl.create_default_context(cafile=ctx.ca_path)
    with urlopen(request, timeout=timeout_s, context=ssl_context) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def build_label_selector(match_labels: Optional[Dict[str, Any]]) -> str:
    if not isinstance(match_labels, dict):
        return ""
    parts: List[str] = []
    for key, value in match_labels.items():
        key_text = str(key or "").strip()
        value_text = str(value or "").strip()
        if key_text and value_text:
            parts.append(f"{key_text}={value_text}")
    return ",".join(parts)


def is_pod_ready(pod: Dict[str, Any]) -> bool:
    status = pod.get("status") or {}
    conditions = status.get("conditions") or []
    for condition in conditions:
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    container_statuses = status.get("containerStatuses") or []
    if container_statuses:
        return all(bool(item.get("ready")) for item in container_statuses)
    return False


def pod_dict_to_state(pod: Dict[str, Any]) -> Dict[str, Any]:
    metadata = pod.get("metadata") or {}
    status = pod.get("status") or {}
    container_statuses = status.get("containerStatuses") or []
    restart_sum = sum(int(item.get("restartCount") or 0) for item in container_statuses)
    deletion_ts = metadata.get("deletionTimestamp")

    phase = status.get("phase")
    detailed_status = status.get("reason") or phase
    if container_statuses:
        for container_status in container_statuses:
            state = container_status.get("state") or {}
            terminated = state.get("terminated")
            waiting = state.get("waiting")
            if terminated and terminated.get("reason"):
                detailed_status = terminated.get("reason")
                break
            if waiting and waiting.get("reason"):
                detailed_status = waiting.get("reason")
                break
    if deletion_ts:
        detailed_status = "Terminating"

    return {
        "name": metadata.get("name"),
        "uid": metadata.get("uid"),
        "phase": phase,
        "status_detail": detailed_status,
        "ready": is_pod_ready(pod),
        "terminating": bool(deletion_ts),
        "restarts": restart_sum,
        "start_time": status.get("startTime"),
    }


class KubernetesPodWatchEngine:
    def __init__(self) -> None:
        self._watch_queue: Queue[Tuple[str, List[Dict[str, Any]]]] = Queue()
        self._watch_pods_state: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._watch_lock = threading.Lock()
        self._watch_threads: Dict[str, threading.Thread] = {}
        self._watch_stop_events: Dict[str, threading.Event] = {}
        self._target_specs: Dict[str, PodWatchTarget] = {}

    def get_api_context(self) -> Optional[KubernetesApiContext]:
        return build_incluster_api_context()

    def ensure_watch(self, target: PodWatchTarget, ctx: Optional[KubernetesApiContext] = None) -> bool:
        current = self._target_specs.get(target.key)
        if current == target and target.key in self._watch_threads and self._watch_threads[target.key].is_alive():
            return True

        api_ctx = ctx or self.get_api_context()
        if not api_ctx:
            return False

        if target.key in self._watch_stop_events:
            self._watch_stop_events[target.key].set()
        stop_event = threading.Event()
        self._watch_stop_events[target.key] = stop_event
        self._target_specs[target.key] = target
        thread = threading.Thread(
            target=self._watch_target_pods_sync,
            args=(target, api_ctx, stop_event),
            daemon=True,
            name=f"k8s-watch-{target.key}",
        )
        self._watch_threads[target.key] = thread
        thread.start()
        return True

    def remove_missing(self, active_keys: set[str]) -> None:
        for key in list(self._watch_threads.keys()):
            if key in active_keys:
                continue
            stop_event = self._watch_stop_events.pop(key, None)
            if stop_event:
                stop_event.set()
            self._watch_threads.pop(key, None)
            self._target_specs.pop(key, None)
            with self._watch_lock:
                self._watch_pods_state.pop(key, None)

    def get_update(self, timeout_s: float = 1.0) -> Tuple[str, List[Dict[str, Any]]]:
        return self._watch_queue.get(timeout=timeout_s)

    def drain_pending(self, max_items: int = 200) -> Dict[str, List[Dict[str, Any]]]:
        coalesced: Dict[str, List[Dict[str, Any]]] = {}
        for _ in range(max_items):
            try:
                key, state = self._watch_queue.get_nowait()
            except Exception:
                break
            coalesced[key] = state
        return coalesced

    def _watch_pods_stream(
        self,
        ctx: KubernetesApiContext,
        namespace: str,
        label_selector: str,
        resource_version: str = "",
        resync_after_seconds: float = WATCH_RESYNC_INTERVAL_S,
    ):
        path = (
            f"/api/v1/namespaces/{namespace}/pods"
            f"?watch=1&labelSelector={quote(label_selector, safe='')}"
        )
        if resource_version is not None:
            path += f"&resourceVersion={quote(str(resource_version), safe='')}"
        request = Request(f"{ctx.base}{path}", method="GET")
        request.add_header("Authorization", f"Bearer {ctx.token}")
        request.add_header("Accept", "application/json")
        ssl_context = ssl.create_default_context(cafile=ctx.ca_path)
        with urlopen(request, timeout=WATCH_READ_TIMEOUT_S, context=ssl_context) as response:
            started = time.monotonic()
            while True:
                if time.monotonic() - started >= resync_after_seconds:
                    return
                line = response.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                event_type = event.get("type")
                obj = event.get("object") or {}
                if obj.get("kind") == "Pod":
                    yield event_type, obj
                if event_type == "ERROR":
                    status = obj.get("status") or {}
                    if status.get("code") == 410:
                        return
                    return

    def _watch_target_pods_sync(
        self,
        target: PodWatchTarget,
        ctx: KubernetesApiContext,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                list_path = (
                    f"/api/v1/namespaces/{target.namespace}/pods"
                    f"?labelSelector={quote(target.label_selector, safe='')}"
                )
                payload = k8s_get_json(ctx, list_path, timeout_s=10.0)
                items = payload.get("items") or []
                resource_version = (payload.get("metadata") or {}).get("resourceVersion") or ""
                with self._watch_lock:
                    self._watch_pods_state[target.key] = {}
                    for pod in items:
                        state = pod_dict_to_state(pod)
                        name = state.get("name")
                        if name:
                            self._watch_pods_state[target.key][name] = state
                    states = list(self._watch_pods_state[target.key].values())
                states.sort(key=lambda item: str(item.get("name") or ""))
                self._watch_queue.put((target.key, states))
                for event_type, pod in self._watch_pods_stream(
                    ctx,
                    target.namespace,
                    target.label_selector,
                    resource_version,
                ):
                    if stop_event.is_set():
                        break
                    name = (pod.get("metadata") or {}).get("name")
                    if not name:
                        continue
                    with self._watch_lock:
                        if target.key not in self._watch_pods_state:
                            self._watch_pods_state[target.key] = {}
                        if event_type == "DELETED":
                            self._watch_pods_state[target.key].pop(name, None)
                        else:
                            self._watch_pods_state[target.key][name] = pod_dict_to_state(pod)
                        states = list(self._watch_pods_state[target.key].values())
                    states.sort(key=lambda item: str(item.get("name") or ""))
                    self._watch_queue.put((target.key, states))
            except Exception:
                if not stop_event.is_set():
                    time.sleep(2)
