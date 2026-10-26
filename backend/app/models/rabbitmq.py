"""
Pydantic models for RabbitMQ monitoring payloads.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from app.models.node_exporter import NodeExporterMetrics
from app.models.infrastructure import InfrastructureMetrics


class RabbitListener(BaseModel):
    """RabbitMQ listener exposed by a node."""
    protocol: Optional[str] = None
    ip_address: Optional[str] = None
    port: int


class RabbitOverview(BaseModel):
    """RabbitMQ cluster-wide overview counters."""
    rabbitmq_version: Optional[str] = None
    erlang_version: Optional[str] = None
    messages: int = 0
    messages_ready: int = 0
    messages_unacknowledged: int = 0
    queues: int = 0
    exchanges: int = 0
    connections: int = 0
    channels: int = 0
    channels_running: Optional[int] = None
    consumers: int = 0
    consumers_total: Optional[int] = None
    publish_rate: Optional[float] = None
    deliver_get_rate: Optional[float] = None
    ack_rate: Optional[float] = None
    redeliver_rate: Optional[float] = None
    listeners: Optional[List[RabbitListener]] = None


class RabbitNode(BaseModel):
    """RabbitMQ node health and resource usage."""
    name: str
    running: bool
    status: str = "UNREACHABLE"
    status_reason: Optional[str] = None
    rabbitmq_version: Optional[str] = None
    erlang_version: Optional[str] = None
    uptime_seconds: Optional[int] = None
    channels: Optional[int] = None
    connections: Optional[int] = None
    role: Optional[str] = None
    mem_alarm: Optional[bool] = None
    disk_free_alarm: Optional[bool] = None
    being_drained: Optional[bool] = None
    partitions: Optional[List[str]] = None
    cluster_links_count: Optional[int] = None
    cluster_link_peers: Optional[List[str]] = None
    enabled_plugins: Optional[List[str]] = None
    mem_used: Optional[int] = None
    mem_limit: Optional[int] = None
    disk_free: Optional[int] = None
    disk_free_limit: Optional[int] = None
    sockets_used: Optional[int] = None
    sockets_total: Optional[int] = None
    fd_used: Optional[int] = None
    fd_total: Optional[int] = None
    node_exporter: Optional[NodeExporterMetrics] = None
    infrastructure: Optional[InfrastructureMetrics] = None


class RabbitQueue(BaseModel):
    """RabbitMQ queue lightweight metrics."""
    name: str
    vhost: str
    state: Optional[str] = None
    type: Optional[str] = None
    durable: Optional[bool] = None
    auto_delete: Optional[bool] = None
    exclusive: Optional[bool] = None
    consumers: int = 0
    messages: int = 0
    messages_ready: int = 0
    messages_unacknowledged: int = 0
    memory_bytes: Optional[int] = None
    consumer_utilisation: Optional[float] = None
    publish_rate: Optional[float] = None
    deliver_get_rate: Optional[float] = None
    ack_rate: Optional[float] = None
    redeliver_rate: Optional[float] = None


class RabbitUser(BaseModel):
    """RabbitMQ user with tags (roles)."""
    name: str
    tags: Optional[str] = None  # Comma-separated tags: administrator, monitoring, management, policymaker


class RabbitCluster(BaseModel):
    """RabbitMQ target data."""
    name: str
    overview: RabbitOverview
    nodes: List[RabbitNode]
    queues: List[RabbitQueue]
    users: Optional[List[RabbitUser]] = None
    last_update: datetime
    ui_url: Optional[str] = None
