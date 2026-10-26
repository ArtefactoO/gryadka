"""
Pydantic models for Redis monitoring payloads.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from app.models.node_exporter import NodeExporterMetrics
from app.models.infrastructure import InfrastructureMetrics


class RedisOverview(BaseModel):
    """Redis target-wide overview counters."""
    mode: str = "sentinel"
    master_name: Optional[str] = None
    master_host: Optional[str] = None
    master_port: Optional[int] = None
    replicas_total: int = 0
    replicas_up: int = 0
    sentinels_total: int = 0
    sentinels_up: int = 0
    connected_clients: int = 0
    ops_per_sec: int = 0
    used_memory_bytes: int = 0
    keys_total: int = 0
    keyspace_hit_rate: Optional[float] = None
    rdb_enabled: Optional[bool] = None
    rdb_bgsave_in_progress: Optional[bool] = None
    rdb_last_bgsave_status: Optional[str] = None
    rdb_last_save_time: Optional[int] = None
    input_kbps: Optional[float] = None
    output_kbps: Optional[float] = None
    blocked_clients: Optional[int] = None
    evicted_keys_rate: Optional[float] = None
    expired_keys_rate: Optional[float] = None
    rejected_connections_rate: Optional[float] = None
    keyspace_hits_rate: Optional[float] = None
    keyspace_misses_rate: Optional[float] = None
    mem_fragmentation_ratio: Optional[float] = None


class RedisNode(BaseModel):
    """Redis/Sentinel node metrics."""
    name: str
    host: str
    port: int
    kind: str  # REDIS or SENTINEL
    role: str  # MASTER, REPLICA, SENTINEL, UNKNOWN
    status: str  # UP, DOWN, UNREACHABLE
    status_reason: Optional[str] = None
    redis_version: Optional[str] = None
    uptime_seconds: Optional[int] = None
    connected_clients: Optional[int] = None
    used_memory_bytes: Optional[int] = None
    used_memory_rss_bytes: Optional[int] = None
    used_memory_peak_bytes: Optional[int] = None
    maxmemory_bytes: Optional[int] = None
    total_memory_bytes: Optional[int] = None
    ops_per_sec: Optional[int] = None
    input_kbps: Optional[float] = None
    output_kbps: Optional[float] = None
    blocked_clients: Optional[int] = None
    evicted_keys_rate: Optional[float] = None
    expired_keys_rate: Optional[float] = None
    rejected_connections_rate: Optional[float] = None
    keyspace_hits_rate: Optional[float] = None
    keyspace_misses_rate: Optional[float] = None
    mem_fragmentation_ratio: Optional[float] = None
    rdb_enabled: Optional[bool] = None
    rdb_bgsave_in_progress: Optional[bool] = None
    rdb_last_bgsave_status: Optional[str] = None
    rdb_last_save_time: Optional[int] = None
    master_host: Optional[str] = None
    master_port: Optional[int] = None
    master_link_status: Optional[str] = None
    replication_offset: Optional[int] = None
    lag_bytes: Optional[int] = None
    node_exporter: Optional[NodeExporterMetrics] = None
    infrastructure: Optional[InfrastructureMetrics] = None


class RedisDatabase(BaseModel):
    """Redis logical database counters."""
    name: str
    keys: int = 0
    expires: int = 0
    avg_ttl: Optional[int] = None


class RedisCluster(BaseModel):
    """Redis target data."""
    name: str
    overview: RedisOverview
    nodes: List[RedisNode]
    databases: List[RedisDatabase]
    last_update: datetime
