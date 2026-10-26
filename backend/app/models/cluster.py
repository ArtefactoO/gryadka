"""
Data models for MongoDB cluster information
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from app.models.mongo_backup import MongoDatabaseBackup
from app.models.node_exporter import NodeExporterMetrics
from app.models.infrastructure import InfrastructureMetrics


class PrimaryHistory(BaseModel):
    """History of PRIMARY node changes"""
    became_primary_at: Optional[datetime] = None
    previous_primary: Optional[str] = None


class NodeSize(BaseModel):
    """Size information for a node"""
    oplog: Optional[int] = None  # Size in bytes
    data: Optional[int] = None    # Size in bytes


class PrimaryMetrics(BaseModel):
    """Additional PRIMARY-only metrics and replica set settings"""
    oplog_configured_bytes: Optional[int] = None
    oplog_used_bytes: Optional[int] = None
    oplog_window_seconds: Optional[int] = None
    election_timeout_millis: Optional[int] = None
    heartbeat_interval_millis: Optional[int] = None
    write_concern_majority_journal_default: Optional[bool] = None


class Node(BaseModel):
    """MongoDB replica set node information"""
    name: str
    stateStr: str  # PRIMARY, SECONDARY, etc. (camelCase for frontend compatibility)
    status: str  # UP or DOWN (from replica set member health)
    lag: int  # Replication lag in seconds
    priority: int
    hidden: int
    uptime: Optional[int] = None
    size: Optional[NodeSize] = None
    primary_metrics: Optional[PrimaryMetrics] = None
    primary_history: Optional[PrimaryHistory] = None
    sync_source_host: Optional[str] = None
    mongo_version: Optional[str] = None
    self: bool = False  # Is this the current node
    lag_history: Optional[List[Dict[str, Any]]] = None  # [{time: ISO str, lag: int}, ...] for chart
    node_exporter: Optional[NodeExporterMetrics] = None
    infrastructure: Optional[InfrastructureMetrics] = None

    class Config:
        # Allow both snake_case and camelCase
        populate_by_name = True


class DatabaseUser(BaseModel):
    """MongoDB user information"""
    user: str
    db: str
    roles: str  # Comma-separated roles


class Cluster(BaseModel):
    """MongoDB cluster information"""
    name: str
    nodes: List[Node]
    databases: List[str]
    users: Optional[List[DatabaseUser]] = None  # Only if authenticated
    users_visible: bool = False
    feature_compatibility_version: Optional[str] = None
    backups: Dict[str, MongoDatabaseBackup] = Field(default_factory=dict)
    last_update: datetime


class Collection(BaseModel):
    """MongoDB collection information"""
    name: str
    count: Optional[int] = None
    size: Optional[int] = None  # Size in bytes
    indexes_count: Optional[int] = None


class CollectionIndex(BaseModel):
    """MongoDB collection index information"""
    name: str
    key: str
    unique: bool = False
    sparse: bool = False
    hidden: bool = False
    ttl_seconds: Optional[int] = None
    partial: bool = False


class ClusterStats(BaseModel):
    """Overall statistics"""
    total_clusters: int
    total_nodes: int
    last_update: datetime


class MonitoringTargetSummary(BaseModel):
    """Generic monitoring target summary for type-driven frontend"""
    id: str
    name: str
    type: str
    status: str
    last_update: Optional[datetime] = None
    group: Optional[str] = None  # e.g. "logging" for redpanda, elasticsearch, kibana


# Status is OK for display when host/target is healthy; not OK => show in unreachable banner.
# MongoDB: OK = PRIMARY, SECONDARY, ARBITER. RabbitMQ: OK = running and status RUNNING. Redis: OK = UP.
# HAProxy: OK = UP. Redpanda/Elasticsearch: OK = green/healthy or single node available.
class UnreachableHost(BaseModel):
    """A host or target that is not in OK status (for top banner)."""
    host: str  # display name (node name, server name, or target name)
    status: str  # actual status string (e.g. DOWN, UNREACHABLE, RECOVERING)
    target_name: str  # monitoring target name (cluster or target)
    target_type: str  # mongodb, rabbitmq, redis, haproxy, redpanda, elasticsearch
