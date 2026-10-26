"""
Service for collecting Argo Rollout information from Kubernetes API.
Supports real-time watch for rollout status changes.
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from kubernetes import client, config, watch
from kubernetes.client import ApiException

from app.config import get_app_config
from app.monitor_logging import log_collector_progress, log_collector_summary, log_monitor_request
from app.models.argo_rollout import (
    AnalysisRun,
    CanaryStep,
    DeploymentInfo,
    ReplicaSetInfo,
    RolloutInfo,
    RolloutStatus,
)

logger = logging.getLogger(__name__)


# Singleton instance
_collector_instance = None

def get_argo_rollout_collector() -> 'ArgoRolloutCollector':
    """Get singleton collector instance."""
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = ArgoRolloutCollector()
    return _collector_instance


class ArgoRolloutCollector:
    """Collect Argo Rollout metrics via Kubernetes API.

    Implemented as singleton to avoid creating multiple K8s clients.
    """

    def __init__(self):
        self._init_kubernetes_client()

    def _init_kubernetes_client(self):
        """Initialize Kubernetes client (in-cluster or local)."""
        try:
            config.load_incluster_config()
            logger.info("Using in-cluster Kubernetes config")
        except Exception:
            try:
                config.load_kube_config()
                logger.info("Using local Kubernetes config")
            except Exception as e:
                logger.error("Failed to load Kubernetes config: %s", e)
                raise

        # Create clients once and reuse
        self.custom_api = client.CustomObjectsApi()
        self.apps_api = client.AppsV1Api()
        self.core_api = client.CoreV1Api()
        logger.info("ArgoRolloutCollector initialized with K8s clients")

    def _get_namespace_from_config(self) -> str:
        """Get namespace from app config."""
        current_config = get_app_config()
        if not current_config:
            return "default"

        for monitor in getattr(current_config, 'monitors', []):
            if monitor.get('name') in {'argo_rollout', 'rollouts'}:
                monitor_config = monitor.get('config', {}) or {}
                if isinstance(monitor_config, dict):
                    targets = monitor_config.get('targets', [])
                    if isinstance(targets, list) and targets:
                        return targets[0].get('namespace', 'default')
                    return monitor_config.get('namespace', 'default')
        return "default"

    def _extract_image(self, containers: List[Dict]) -> str:
        """Extract image from container spec."""
        if not containers:
            log_collector_progress(logger, "argo-rollout", "container", "no_containers")
            return "unknown"
        image = containers[0].get("image", "unknown")
        log_collector_progress(logger, "argo-rollout", "container", "image_extracted", image=image)
        if ":" in image:
            return image.rsplit(":", 1)[-1]
        return image

    @staticmethod
    def _build_label_selector(match_labels: Optional[Dict[str, str]]) -> str:
        if not isinstance(match_labels, dict):
            return ""
        parts = []
        for key, value in match_labels.items():
            key_text = str(key or "").strip()
            value_text = str(value or "").strip()
            if key_text and value_text:
                parts.append(f"{key_text}={value_text}")
        return ",".join(parts)

    @staticmethod
    def _parse_pod_state(pod: client.V1Pod) -> Dict[str, Any]:
        metadata = pod.metadata
        status = pod.status
        container_statuses = status.container_statuses or []
        ready = bool(container_statuses) and all(bool(item.ready) for item in container_statuses)
        restarts = sum(int(item.restart_count or 0) for item in container_statuses) if container_statuses else 0
        terminating = bool(getattr(metadata, "deletion_timestamp", None))
        detailed_status = status.phase or "Unknown"
        if terminating:
            detailed_status = "Terminating"
        elif container_statuses:
            waiting = next((item.state.waiting for item in container_statuses if item.state and item.state.waiting), None)
            if waiting and waiting.reason:
                detailed_status = waiting.reason
            elif not ready and status.phase == "Running":
                detailed_status = "Not Ready"

        return {
            "name": metadata.name if metadata else None,
            "uid": metadata.uid if metadata else None,
            "phase": status.phase if status else None,
            "status_detail": detailed_status,
            "ready": ready,
            "terminating": terminating,
            "restarts": restarts,
            "start_time": status.start_time.isoformat() if status and status.start_time else None,
        }

    def _get_pods_by_selector(self, namespace: str, label_selector: str) -> List[Dict[str, Any]]:
        if not label_selector:
            return []
        try:
            pod_list = self.core_api.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
            pods = [self._parse_pod_state(pod) for pod in pod_list.items]
            pods.sort(key=lambda item: (0 if item.get("terminating") else 1, 0 if not item.get("ready") else 1, str(item.get("name") or "")))
            return pods
        except Exception as e:
            logger.error("Error getting pods for selector %s in %s: %s", label_selector, namespace, e)
            return []

    def _get_rollout_image(self, rollout: Dict[str, Any], namespace: str, rollout_name: str) -> str:
        """Get image from rollout spec or referenced workload (Deployment)."""
        spec = rollout.get("spec", {})

        # First try to get from inline template
        template = spec.get("template", {})
        pod_spec = template.get("spec", {})
        containers = pod_spec.get("containers", [])
        if containers:
            return self._extract_image(containers)

        # If no inline template, check workloadRef
        workload_ref = spec.get("workloadRef")
        if workload_ref:
            try:
                workload_name = workload_ref.get("name")
                workload_kind = workload_ref.get("kind", "Deployment")

                if workload_kind == "Deployment":
                    deployment = self.apps_api.read_namespaced_deployment(workload_name, namespace)
                    if deployment.spec and deployment.spec.template and deployment.spec.template.spec:
                        containers = deployment.spec.template.spec.containers
                        if containers:
                            return self._extract_image([{"image": c.image} for c in containers])
            except Exception as e:
                logger.debug("Could not get image from workloadRef for rollout %s: %s", rollout_name, e)

        logger.debug("No image found for rollout %s", rollout_name)
        return "unknown"

    def _parse_replicaset_info(self, rs: Dict[str, Any]) -> ReplicaSetInfo:
        """Parse ReplicaSet into ReplicaSetInfo model."""
        metadata = rs.get("metadata", {})
        spec = rs.get("spec", {})
        status = rs.get("status", {})

        revision = int(metadata.get("annotations", {}).get("deployment.kubernetes.io/revision", "0"))

        template = spec.get("template", {})
        pod_spec = template.get("spec", {})
        containers = pod_spec.get("containers", [])
        image = self._extract_image(containers)

        created_at = metadata.get("creationTimestamp")
        if created_at:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created_at = datetime.now(timezone.utc)

        return ReplicaSetInfo(
            name=metadata.get("name", ""),
            revision=revision,
            replicas=int(status.get("replicas", 0) or 0),
            available_replicas=int(status.get("availableReplicas", 0) or 0),
            image=image,
            created_at=created_at,
        )

    def _parse_rollout_status(self, rollout: Dict[str, Any]) -> RolloutStatus:
        """Determine rollout status from phase."""
        status = rollout.get("status", {})
        phase = status.get("phase", "")

        if phase == "Healthy":
            return RolloutStatus.HEALTHY
        elif phase == "Progressing":
            return RolloutStatus.PROGRESSING
        elif phase == "Paused":
            return RolloutStatus.PAUSED
        elif phase == "Degraded":
            return RolloutStatus.DEGRADED
        else:
            conditions = status.get("conditions", [])
            for condition in conditions:
                if condition.get("type") == "Progressing" and condition.get("status") == "True":
                    return RolloutStatus.PROGRESSING
            return RolloutStatus.HEALTHY

    def _get_replicasets_for_rollout(self, namespace: str, rollout_name: str) -> List[ReplicaSetInfo]:
        """Get ReplicaSets associated with rollout."""
        try:
            rs_list = self.apps_api.list_namespaced_replica_set(namespace)
            replicasets = []

            for rs in rs_list.items:
                owner_refs = rs.metadata.owner_references or []
                for owner in owner_refs:
                    if owner.name == rollout_name:
                        rs_dict = {
                            "metadata": {
                                "name": rs.metadata.name,
                                "creationTimestamp": rs.metadata.creation_timestamp.isoformat() if rs.metadata.creation_timestamp else None,
                                "annotations": rs.metadata.annotations or {},
                            },
                            "spec": {
                                "template": {
                                    "spec": {
                                        "containers": [
                                            {"image": c.image} for c in (rs.spec.template.spec.containers if rs.spec.template and rs.spec.template.spec else [])
                                        ]
                                    }
                                } if rs.spec and rs.spec.template else {}
                            },
                            "status": {
                                "replicas": rs.status.replicas if rs.status else 0,
                                "availableReplicas": rs.status.available_replicas if rs.status else 0,
                            }
                        }
                        replicasets.append(self._parse_replicaset_info(rs_dict))
                        break

            # Sort by revision desc, take current + 3 previous
            replicasets.sort(key=lambda x: x.revision, reverse=True)
            return replicasets[:4]

        except Exception as e:
            logger.error("Error getting ReplicaSets for rollout %s: %s", rollout_name, e)
            return []

    def _parse_rollout_info(self, rollout: Dict[str, Any], namespace: str, all_replicasets: Optional[List[Dict[str, Any]]] = None, include_replicasets: bool = True) -> RolloutInfo:
        """Parse rollout dict into RolloutInfo model.

        Args:
            rollout: Rollout data dict
            namespace: Namespace name
            all_replicasets: Pre-fetched ReplicaSets list (optional)
            include_replicasets: Whether to include ReplicaSets (expensive)
        """
        metadata = rollout.get("metadata", {})
        spec = rollout.get("spec", {})
        status = rollout.get("status", {})

        name = metadata.get("name", "")

        # Get replicas
        replicas = spec.get("replicas", 0)
        available = status.get("availableReplicas", 0)
        ready = status.get("readyReplicas", 0)

        # Get current revision
        current_revision = status.get("currentPodHash", "")

        # Get current image from template or workloadRef
        current_image = self._get_rollout_image(rollout, namespace, name)

        # Get strategy
        strategy = spec.get("strategy", {})
        strategy_type = "Canary" if "canary" in strategy else "BlueGreen"

        # Get ReplicaSets only if requested (expensive operation)
        replicasets = []
        if include_replicasets:
            if all_replicasets is not None:
                replicasets = self._filter_replicasets_for_rollout(name, all_replicasets)
            else:
                replicasets = self._get_replicasets_for_rollout(namespace, name)

        # Get message from status
        message = status.get("message", "")

        return RolloutInfo(
            name=name,
            namespace=namespace,
            status=self._parse_rollout_status(rollout),
            strategy=strategy_type,
            current_replicas=ready,
            desired_replicas=replicas,
            available_replicas=available,
            current_revision=hash(current_revision) % 10000 if current_revision else 0,
            current_image=current_image,
            replicasets=replicasets,
            message=message,
            k8s_pods=None,
        )

    def _filter_replicasets_for_rollout(self, rollout_name: str, all_replicasets: List[Dict[str, Any]]) -> List[ReplicaSetInfo]:
        """Filter ReplicaSets for a specific rollout from pre-fetched list."""
        replicasets = []

        for rs in all_replicasets:
            owner_refs = rs.get("metadata", {}).get("ownerReferences", [])
            for owner in owner_refs:
                if owner.get("name") == rollout_name and owner.get("kind") == "Rollout":
                    try:
                        replicasets.append(self._parse_replicaset_info(rs))
                    except Exception as e:
                        logger.error("Error parsing ReplicaSet %s: %s", rs.get("metadata", {}).get("name", "unknown"), e)
                    break

        # Sort by revision desc, take current + 1 previous (reduced for memory)
        replicasets.sort(key=lambda x: x.revision, reverse=True)
        return replicasets[:2]

    def get_rollouts(self, namespace: Optional[str] = None) -> List[RolloutInfo]:
        """Get all rollouts in namespace."""
        if namespace is None:
            namespace = self._get_namespace_from_config()

        try:
            log_monitor_request(logger, "argo-rollout", "list_rollouts", endpoint=namespace)
            rollouts = self.custom_api.list_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts"
            )

            items = rollouts.get("items", [])
            log_collector_progress(logger, "argo-rollout", namespace, "rollouts_listed", count=len(items))

            # Skip ReplicaSets for list view (too expensive with 4000+ ReplicaSets)
            # ReplicaSets will be loaded on-demand for individual rollout details
            result = []
            for rollout in items:
                try:
                    info = self._parse_rollout_info(rollout, namespace, include_replicasets=False)
                    result.append(info)
                except Exception as e:
                    logger.error("Error parsing rollout %s: %s", rollout.get("metadata", {}).get("name", "unknown"), e)

            log_collector_progress(logger, "argo-rollout", namespace, "rollouts_parsed", count=len(result))
            return result

        except ApiException as e:
            logger.error("Kubernetes API error getting rollouts from %s: %s", namespace, e)
            return []
        except Exception as e:
            logger.error("Error getting rollouts from %s: %s", namespace, e)
            return []

    def _get_all_replicasets_for_namespace(self, namespace: str) -> List[Dict[str, Any]]:
        """Get all ReplicaSets in namespace as dicts."""
        try:
            rs_list = self.apps_api.list_namespaced_replica_set(namespace)
            replicasets = []

            for rs in rs_list.items:
                rs_dict = {
                    "metadata": {
                        "name": rs.metadata.name,
                        "creationTimestamp": rs.metadata.creation_timestamp.isoformat() if rs.metadata.creation_timestamp else None,
                        "annotations": rs.metadata.annotations or {},
                        "ownerReferences": [
                            {"name": owner.name, "kind": owner.kind} for owner in (rs.metadata.owner_references or [])
                        ],
                    },
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {"image": c.image} for c in (rs.spec.template.spec.containers if rs.spec.template and rs.spec.template.spec else [])
                                ]
                            }
                        } if rs.spec and rs.spec.template else {}
                    },
                    "status": {
                        "replicas": rs.status.replicas if rs.status else 0,
                        "availableReplicas": rs.status.available_replicas if rs.status else 0,
                    }
                }
                replicasets.append(rs_dict)

            return replicasets
        except Exception as e:
            logger.error("Error fetching ReplicaSets for namespace %s: %s", namespace, e)
            return []

    def get_deployments_without_rollout(self, namespace: Optional[str] = None) -> List[DeploymentInfo]:
        """Get deployments that don't have Argo Rollout."""
        if namespace is None:
            namespace = self._get_namespace_from_config()

        try:
            # Get all rollouts to find managed deployments
            rollouts = self.custom_api.list_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts"
            )

            rollout_names = {r["metadata"]["name"] for r in rollouts.get("items", [])}
            log_collector_progress(logger, "argo-rollout", namespace, "rollout_exclusions_built", count=len(rollout_names))

            # Get all deployments
            log_monitor_request(logger, "argo-rollout", "list_deployments", endpoint=namespace)
            deployments = self.apps_api.list_namespaced_deployment(namespace)
            log_collector_progress(logger, "argo-rollout", namespace, "deployments_listed", count=len(deployments.items))

            result = []
            for dep in deployments.items:
                if dep.metadata.name not in rollout_names:
                    # Get image
                    containers = dep.spec.template.spec.containers if dep.spec and dep.spec.template and dep.spec.template.spec else []
                    image = self._extract_image([{"image": c.image} for c in containers])

                    result.append(DeploymentInfo(
                        name=dep.metadata.name,
                        namespace=namespace,
                        replicas=int(dep.spec.replicas) if dep.spec and dep.spec.replicas is not None else 0,
                        available_replicas=int(dep.status.available_replicas) if dep.status and dep.status.available_replicas is not None else 0,
                        current_image=image,
                        strategy=str(dep.spec.strategy.type) if dep.spec and dep.spec.strategy else "RollingUpdate",
                        created_at=dep.metadata.creation_timestamp if dep.metadata.creation_timestamp else datetime.now(timezone.utc),
                        k8s_pods=None,
                    ))

            return result

        except Exception as e:
            logger.error("Error getting deployments: %s", e)
            return []

    def get_workload_watch_specs(self, namespace: Optional[str] = None) -> List[Dict[str, str]]:
        """Return rollout/deployment selectors for pod-level watch SSE."""
        if namespace is None:
            namespace = self._get_namespace_from_config()

        specs: List[Dict[str, str]] = []
        rollout_names: set[str] = set()

        try:
            rollouts = self.custom_api.list_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
            )
            for rollout in rollouts.get("items", []):
                name = str((rollout.get("metadata") or {}).get("name") or "").strip()
                if not name:
                    continue
                rollout_names.add(name)
                selector = self._build_label_selector(((rollout.get("spec") or {}).get("selector") or {}).get("matchLabels"))
                if not selector:
                    continue
                specs.append(
                    {
                        "key": f"rollout:{name}",
                        "workload_kind": "rollout",
                        "workload_name": name,
                        "label_selector": selector,
                    }
                )
        except Exception as e:
            logger.error("Error listing rollout watch specs for %s: %s", namespace, e)

        try:
            deployments = self.apps_api.list_namespaced_deployment(namespace)
            for dep in deployments.items:
                name = str(dep.metadata.name or "").strip()
                if not name or name in rollout_names:
                    continue
                selector = self._build_label_selector(dep.spec.selector.match_labels if dep.spec and dep.spec.selector else {})
                if not selector:
                    continue
                specs.append(
                    {
                        "key": f"deployment:{name}",
                        "workload_kind": "deployment",
                        "workload_name": name,
                        "label_selector": selector,
                    }
                )
        except Exception as e:
            logger.error("Error listing deployment watch specs for %s: %s", namespace, e)

        return specs

    def watch_rollouts(self, namespace: Optional[str] = None, timeout: int = 300):
        """Watch for rollout changes (generator)."""
        if namespace is None:
            namespace = self._get_namespace_from_config()

        w = watch.Watch()
        try:
            for event in w.stream(
                self.custom_api.list_namespaced_custom_object,
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
                timeout_seconds=timeout
            ):
                yield event
        except Exception as e:
            logger.error("Error watching rollouts: %s", e)
            raise

    def promote_rollout(self, namespace: str, rollout_name: str) -> bool:
        """Promote rollout (continue canary)."""
        try:
            patch = {"spec": {"paused": False}}
            self.custom_api.patch_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
                name=rollout_name,
                body=patch
            )
            return True
        except Exception as e:
            logger.error("Error promoting rollout %s: %s", rollout_name, e)
            return False

    def promote_full_rollout(self, namespace: str, rollout_name: str) -> bool:
        """Promote rollout immediately, skipping remaining steps."""
        try:
            # Argo Rollouts full promotion requires unpausing the rollout as well.
            self.custom_api.patch_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
                name=rollout_name,
                body={"spec": {"paused": False}},
            )
            self.custom_api.patch_namespaced_custom_object_status(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
                name=rollout_name,
                body={"status": {"promoteFull": True}},
            )
            return True
        except Exception as e:
            logger.error("Error promoting rollout full %s: %s", rollout_name, e)
            return False

    def abort_rollout(self, namespace: str, rollout_name: str) -> bool:
        """Abort rollout."""
        try:
            self.custom_api.patch_namespaced_custom_object_status(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
                name=rollout_name,
                body={"status": {"abort": True}}
            )
            return True
        except Exception as e:
            logger.error("Error aborting rollout %s: %s", rollout_name, e)
            return False

    def retry_rollout(self, namespace: str, rollout_name: str) -> bool:
        """Retry rollout by clearing terminal status flags."""
        try:
            self.custom_api.patch_namespaced_custom_object_status(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
                name=rollout_name,
                body={"status": {"abort": False, "promoteFull": False}},
            )
            return True
        except Exception as e:
            logger.error("Error retrying rollout %s: %s", rollout_name, e)
            return False

    def restart_rollout(self, namespace: str, rollout_name: str) -> bool:
        """Restart rollout pods by setting spec.restartAt."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.custom_api.patch_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
                name=rollout_name,
                body={"spec": {"restartAt": now}},
            )
            return True
        except Exception as e:
            logger.error("Error restarting rollout %s: %s", rollout_name, e)
            return False

    def restart_deployment(self, namespace: str, deployment_name: str) -> bool:
        """Restart deployment (for services without rollout)."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": now
                            }
                        }
                    }
                }
            }
            self.apps_api.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=patch
            )
            return True
        except Exception as e:
            logger.error("Error restarting deployment %s: %s", deployment_name, e)
            return False

    def get_rollout_detail(self, namespace: str, rollout_name: str) -> Optional[RolloutInfo]:
        try:
            rollout = self.custom_api.get_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="rollouts",
                name=rollout_name,
            )
            info = self._parse_rollout_info(rollout, namespace, include_replicasets=True)
            selector = self._build_label_selector(((rollout.get("spec") or {}).get("selector") or {}).get("matchLabels"))
            return info.model_copy(update={"k8s_pods": self._get_pods_by_selector(namespace, selector)})
        except Exception as e:
            logger.error("Error getting rollout detail %s: %s", rollout_name, e)
            return None

    def get_deployment_detail(self, namespace: str, deployment_name: str) -> Optional[DeploymentInfo]:
        try:
            dep = self.apps_api.read_namespaced_deployment(name=deployment_name, namespace=namespace)
            containers = dep.spec.template.spec.containers if dep.spec and dep.spec.template and dep.spec.template.spec else []
            image = self._extract_image([{"image": c.image} for c in containers])
            selector = self._build_label_selector(dep.spec.selector.match_labels if dep.spec and dep.spec.selector else {})
            return DeploymentInfo(
                name=dep.metadata.name,
                namespace=namespace,
                replicas=int(dep.spec.replicas) if dep.spec and dep.spec.replicas is not None else 0,
                available_replicas=int(dep.status.available_replicas) if dep.status and dep.status.available_replicas is not None else 0,
                current_image=image,
                strategy=str(dep.spec.strategy.type) if dep.spec and dep.spec.strategy else "RollingUpdate",
                created_at=dep.metadata.creation_timestamp if dep.metadata.creation_timestamp else datetime.now(timezone.utc),
                k8s_pods=self._get_pods_by_selector(namespace, selector),
            )
        except Exception as e:
            logger.error("Error getting deployment detail %s: %s", deployment_name, e)
            return None


    def collect_target_data(self, target_name: str) -> Optional[Dict[str, Any]]:
        """Collect all Argo Rollout data for a target (namespace).

        Args:
            target_name: The namespace to collect data from

        Returns:
            Dict with rollouts and deployments, or None if error
        """
        try:
            # target_name is the namespace
            namespace = target_name

            # Get rollouts
            rollouts = self.get_rollouts(namespace)

            # Get deployments without rollout
            deployments = self.get_deployments_without_rollout(namespace)

            result = {
                "rollouts": [r.model_dump() for r in rollouts],
                "deployments": [d.model_dump() for d in deployments],
                "namespace": namespace,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            log_collector_summary(
                logger,
                "argo-rollout",
                "namespace_collected",
                targets=[namespace],
                rollouts=len(rollouts),
                deployments=len(deployments),
            )
            return result
        except Exception as e:
            logger.error("Error collecting Argo Rollout data for namespace %s: %s", target_name, e)
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())
            return None
