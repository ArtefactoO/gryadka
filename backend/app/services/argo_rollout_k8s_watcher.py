from __future__ import annotations

import asyncio
import json
import logging
from queue import Empty
from typing import Dict, List

from app.services.argo_rollout_collector import ArgoRolloutCollector
from app.services.kubernetes_pod_watch import KubernetesPodWatchEngine, PodWatchTarget
from app.services.sse_manager import sse_manager

logger = logging.getLogger(__name__)

WATCH_INTERVAL_SECONDS = 1.0
TARGETS_RESYNC_INTERVAL_S = 20.0


class ArgoRolloutKubernetesWatcher:
    def __init__(self) -> None:
        self._collector = ArgoRolloutCollector()
        self._watch_engine = KubernetesPodWatchEngine()
        self._watch_meta: Dict[str, Dict[str, str]] = {}
        self._last_pods_signatures: Dict[str, str] = {}
        self._last_targets_sync_monotonic = 0.0

    async def _publish_change_event(self, meta: Dict[str, str], namespace: str, pods_state: List[Dict[str, object]]) -> None:
        target_name = "deployment" if meta["workload_kind"] == "deployment" else "argo-rollout"
        await sse_manager.publish(
            {
                "event": "argo_rollout.k8s.changed",
                "domain": "kubernetes.discovery",
                "target_type": "argo_rollout",
                "target_name": target_name,
                "workload_kind": meta["workload_kind"],
                "workload_name": meta["workload_name"],
                "namespace": namespace,
                "pods_state": pods_state,
            }
        )

    async def run_forever(self, namespace: str) -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                ctx = self._watch_engine.get_api_context()
                if not ctx:
                    await asyncio.sleep(WATCH_INTERVAL_SECONDS)
                    continue

                now = asyncio.get_running_loop().time()
                if (not self._watch_meta) or (now - self._last_targets_sync_monotonic >= TARGETS_RESYNC_INTERVAL_S):
                    specs = await asyncio.to_thread(self._collector.get_workload_watch_specs, namespace)
                    active_keys: set[str] = set()
                    next_meta: Dict[str, Dict[str, str]] = {}
                    for spec in specs:
                        key = spec.get("key")
                        selector = spec.get("label_selector")
                        if not key or not selector:
                            continue
                        active_keys.add(key)
                        next_meta[key] = {
                            "workload_kind": str(spec.get("workload_kind") or ""),
                            "workload_name": str(spec.get("workload_name") or ""),
                        }
                        self._watch_engine.ensure_watch(
                            PodWatchTarget(key=key, namespace=namespace, label_selector=selector),
                            ctx,
                        )
                    self._watch_engine.remove_missing(active_keys)
                    self._watch_meta = next_meta
                    self._last_pods_signatures = {
                        key: signature for key, signature in self._last_pods_signatures.items() if key in active_keys
                    }
                    self._last_targets_sync_monotonic = now

                try:
                    watch_key, pods_state = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: self._watch_engine.get_update(timeout_s=1.0)),
                        2.0,
                    )
                    coalesced: Dict[str, List[Dict[str, object]]] = {watch_key: pods_state}
                    coalesced.update(self._watch_engine.drain_pending(max_items=200))
                    for key, state in coalesced.items():
                        meta = self._watch_meta.get(key)
                        if not meta:
                            continue
                        signature = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
                        if self._last_pods_signatures.get(key) == signature:
                            continue
                        self._last_pods_signatures[key] = signature
                        await self._publish_change_event(meta, namespace, state)
                except (Empty, asyncio.TimeoutError):
                    await asyncio.sleep(0.05)
            except Exception as e:
                logger.warning("Argo rollout Kubernetes watcher loop failed: %s", e)
                await asyncio.sleep(WATCH_INTERVAL_SECONDS)


argo_rollout_k8s_watcher = ArgoRolloutKubernetesWatcher()
