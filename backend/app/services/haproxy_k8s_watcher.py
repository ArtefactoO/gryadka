"""
Realtime watcher for HAProxy Kubernetes-backed targets.
Uses Kubernetes Watch API (like Lens/k9s/Argo) for immediate pod updates;
publishes SSE and refreshes cache on each change.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from queue import Empty
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from app.config import get_app_config, settings
from app.services.cache import CacheService
from app.services.haproxy_collector import HAProxyCollector
from app.services.kubernetes_pod_watch import (
    KubernetesPodWatchEngine,
    PodWatchTarget,
    build_incluster_api_context,
    build_label_selector,
    k8s_get_json,
)
from app.services.kubernetes_pod_resources import enrich_pods_with_resources
from app.services.sse_manager import sse_manager

logger = logging.getLogger(__name__)

WATCH_INTERVAL_SECONDS = 1.0
HAPROXY_CACHE_TTL_S = 60


class HAProxyKubernetesWatcher:
    def __init__(self) -> None:
        self._collector = HAProxyCollector()
        self._haproxy_cache = CacheService(
            db=settings.redis_haproxy_db if settings.redis_haproxy_db is not None else settings.redis_db
        )
        self._watch_engine = KubernetesPodWatchEngine()

    def _get_service_label_selector(
        self, namespace: str, service_name: str
    ) -> Optional[str]:
        """Return labelSelector query string for the service, or None."""
        ctx = build_incluster_api_context()
        if not ctx:
            return None
        service_payload = k8s_get_json(ctx, f"/api/v1/namespaces/{namespace}/services/{service_name}")
        selector_map = ((service_payload.get("spec") or {}).get("selector")) or {}
        if not selector_map:
            return None
        return build_label_selector(selector_map)

    def _discover_endpoint_pod_names(
        self,
        namespace: str,
        service_name: str,
    ) -> List[str]:
        pod_names = set()
        ctx = build_incluster_api_context()
        if not ctx:
            return []
        selector = quote(f"kubernetes.io/service-name={service_name}", safe="")
        try:
            payload = k8s_get_json(
                ctx,
                f"/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices?labelSelector={selector}",
            )
            for item in payload.get("items", []):
                for endpoint in item.get("endpoints", []):
                    pod_name = ((endpoint.get("targetRef") or {}).get("name"))
                    if pod_name:
                        pod_names.add(str(pod_name))
        except Exception:
            pass
        if pod_names:
            return sorted(pod_names)

        try:
            payload = k8s_get_json(ctx, f"/api/v1/namespaces/{namespace}/endpoints/{service_name}")
            for subset in payload.get("subsets", []):
                for addr in subset.get("addresses", []):
                    pod_name = ((addr.get("targetRef") or {}).get("name")) or addr.get("hostname")
                    if pod_name:
                        pod_names.add(str(pod_name))
        except Exception:
            pass
        return sorted(pod_names)

    def _target_namespace(self, target_name: str) -> Optional[str]:
        config = get_app_config()
        if not config:
            return None
        target = config.get_haproxy_target_by_name(target_name)
        options = (target.options if target else {}) or {}
        k8s = options.get("kubernetes") or {}
        namespace = str(k8s.get("namespace") or "").strip()
        return namespace or None

    async def _collect_and_merge_into_cache(self, cache_key: str, target_name: str) -> None:
        """Run collector in thread and merge result into cache, keeping existing k8s_pods (may be newer)."""
        try:
            fresh = await asyncio.to_thread(self._collector.collect_target_data, target_name)
            if not fresh:
                return
            raw = self._haproxy_cache.redis_client.get(cache_key)
            if raw:
                try:
                    data = json.loads(raw)
                    # Keep current k8s_pods so we do not overwrite with stale data from rollout start
                    fresh["k8s_pods"] = data.get("k8s_pods")
                    fresh["last_update"] = datetime.now(timezone.utc).isoformat()
                except (json.JSONDecodeError, TypeError):
                    pass
            self._haproxy_cache.redis_client.setex(
                cache_key,
                HAPROXY_CACHE_TTL_S,
                json.dumps(fresh, default=str),
            )
        except Exception as e:
            logger.warning("HAProxy collector background merge failed for %s: %s", target_name, e)

    async def _publish_change_event(self, target_name: str, pod_names: List[str], pods_state: List[Dict[str, Any]]) -> None:
        await sse_manager.publish(
            {
                "event": "haproxy.k8s.changed",
                "domain": "kubernetes.discovery",
                "target_type": "haproxy",
                "target_name": target_name,
                "pod_names": pod_names,
                "pods_state": pods_state,
            }
        )

    async def _process_watch_update(self, target_name: str, pods_state: List[Dict[str, Any]]) -> None:
        """Update Redis and publish SSE for one Watch update."""
        cache_key = f"haproxy:target:{target_name}"
        namespace = self._target_namespace(target_name)
        if namespace:
            pods_state = await asyncio.to_thread(enrich_pods_with_resources, namespace, pods_state)
        pod_names = [p.get("name") for p in pods_state if p.get("name")]
        raw = self._haproxy_cache.redis_client.get(cache_key)
        if raw:
            try:
                data = json.loads(raw)
                data["k8s_pods"] = pods_state
                data["last_update"] = datetime.now(timezone.utc).isoformat()
                self._haproxy_cache.redis_client.setex(
                    cache_key,
                    HAPROXY_CACHE_TTL_S,
                    json.dumps(data, default=str),
                )
            except (json.JSONDecodeError, TypeError):
                raw = None
        if not raw:
            minimal = {
                "name": target_name,
                "overview": {"total_proxies": 0, "total_servers": 0, "servers_up": 0, "servers_down": 0},
                "proxies": [],
                "pods": [],
                "k8s_pods": pods_state,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            self._haproxy_cache.redis_client.setex(
                cache_key,
                HAPROXY_CACHE_TTL_S,
                json.dumps(minimal, default=str),
            )
        await self._publish_change_event(target_name, pod_names, pods_state)

    async def run_forever(self) -> None:
        """Use Kubernetes Watch API (like Lens/k9s/Argo): one thread per target streams pod events; main loop updates Redis + SSE."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                ctx = self._watch_engine.get_api_context()
                if not ctx:
                    await asyncio.sleep(WATCH_INTERVAL_SECONDS)
                    continue
                config = get_app_config()
                if not config:
                    await asyncio.sleep(WATCH_INTERVAL_SECONDS)
                    continue
                for target in config.haproxy_targets:
                    options = target.options or {}
                    k8s = options.get("kubernetes") or {}
                    if not bool(k8s.get("enabled")):
                        continue
                    namespace = str(k8s.get("namespace") or "").strip()
                    service_name = str(k8s.get("serviceName") or "").strip()
                    if not namespace or not service_name:
                        continue
                    name = target.name
                    selector = self._get_service_label_selector(namespace, service_name)
                    if not selector:
                        continue
                    self._watch_engine.ensure_watch(
                        PodWatchTarget(key=name, namespace=namespace, label_selector=selector),
                        ctx,
                    )
                try:
                    target_name, pods_state = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: self._watch_engine.get_update(timeout_s=1.0)),
                        2.0,
                    )
                    # Coalesce: drain pending updates for any target so we only do one Redis+SSE per target per burst
                    coalesced: Dict[str, List[Dict[str, Any]]] = {target_name: pods_state}
                    coalesced.update(self._watch_engine.drain_pending(max_items=200))
                    for t, s in coalesced.items():
                        await self._process_watch_update(t, s)
                except (Empty, asyncio.TimeoutError):
                    # Periodically run collector so overview/proxies in cache stay fresh
                    if not hasattr(self, "_collect_ticks"):
                        self._collect_ticks = 0
                    self._collect_ticks += 1
                    if self._collect_ticks >= 30:
                        self._collect_ticks = 0
                        for target in config.haproxy_targets:
                            opts = (target.options or {}).get("kubernetes") or {}
                            if opts.get("enabled"):
                                asyncio.create_task(
                                    self._collect_and_merge_into_cache(f"haproxy:target:{target.name}", target.name)
                                )
                    pass
            except Exception as e:
                logger.warning("HAProxy Kubernetes watcher loop failed: %s", e)
                await asyncio.sleep(WATCH_INTERVAL_SECONDS)


haproxy_k8s_watcher = HAProxyKubernetesWatcher()
