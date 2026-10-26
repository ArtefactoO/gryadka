"""
Pydantic models for HAProxy monitoring data.
"""
from typing import List, Optional
from pydantic import BaseModel

from app.models.kubernetes_resources import KubernetesPodResources


class K8sPodState(BaseModel):
    """Kubernetes pod state for live UI (Ready / Not Ready / Terminating)."""
    name: Optional[str] = None
    uid: Optional[str] = None
    phase: Optional[str] = None
    status_detail: Optional[str] = None
    ready: bool = False
    terminating: bool = False
    restarts: Optional[int] = None
    start_time: Optional[str] = None
    resources: Optional[KubernetesPodResources] = None


class HAProxyServer(BaseModel):
    """HAProxy backend server."""
    server_name: str
    status: str
    weight: Optional[int] = None
    act: Optional[int] = None
    bck: Optional[int] = None
    scur: Optional[int] = None  # current sessions
    smax: Optional[int] = None  # max sessions
    slim: Optional[int] = None  # configured session limit
    stot: Optional[int] = None  # total sessions
    bin: Optional[int] = None  # bytes in
    bout: Optional[int] = None  # bytes out
    check_status: Optional[str] = None
    check_code: Optional[int] = None
    downtime: Optional[int] = None
    lastchg: Optional[int] = None


class HAProxyProxy(BaseModel):
    """HAProxy proxy (backend/frontend)."""
    name: str
    total_servers: int
    servers_up: int
    servers_down: int
    sessions_current: Optional[int] = None
    port: Optional[int] = None  # frontend bind port from ConfigMap
    servers: List[HAProxyServer]


class HAProxyOverview(BaseModel):
    """HAProxy overview statistics."""
    total_proxies: int
    total_servers: int
    servers_up: int
    servers_down: int
    total_sessions: Optional[int] = None
    frontend_sessions: Optional[int] = None
    frontend_sessions_limit: Optional[int] = None
    backend_sessions: Optional[int] = None
    current_connections: Optional[int] = None
    connections_limit: Optional[int] = None
    version: Optional[str] = None


class HAProxyPod(BaseModel):
    """HAProxy per-pod (endpoint) data."""
    url: str
    label: str
    overview: HAProxyOverview
    proxies: List[HAProxyProxy]


class HAProxyIngress(BaseModel):
    """Kubernetes Ingress URL verified to route to this HAProxy service."""
    name: str
    namespace: str
    host: str
    path: Optional[str] = None
    path_type: Optional[str] = None
    url: str
    service_name: str
    service_port: Optional[str] = None


class HAProxyCluster(BaseModel):
    """HAProxy cluster data."""
    name: str
    overview: HAProxyOverview
    proxies: List[HAProxyProxy]
    pods: Optional[List[HAProxyPod]] = None
    k8s_pods: Optional[List[K8sPodState]] = None
    ingresses: Optional[List[HAProxyIngress]] = None
    last_update: Optional[str] = None
    restart_supported: Optional[bool] = None
