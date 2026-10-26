"""
Shared Kubernetes resource usage models.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class KubernetesContainerResources(BaseModel):
    """Resource usage/request/limit for one container."""

    name: str
    cpu_usage_cores: Optional[float] = None
    cpu_request_cores: Optional[float] = None
    cpu_limit_cores: Optional[float] = None
    memory_usage_bytes: Optional[int] = None
    memory_request_bytes: Optional[int] = None
    memory_limit_bytes: Optional[int] = None


class KubernetesPodResources(BaseModel):
    """Aggregated resource usage/request/limit for one pod."""

    namespace: str
    pod: str
    cpu_usage_cores: Optional[float] = None
    cpu_request_cores: Optional[float] = None
    cpu_limit_cores: Optional[float] = None
    memory_usage_bytes: Optional[int] = None
    memory_request_bytes: Optional[int] = None
    memory_limit_bytes: Optional[int] = None
    containers: List[KubernetesContainerResources] = Field(default_factory=list)
