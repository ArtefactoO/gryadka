"""
Shared node exporter enrichment models.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class NodeExporterDiskFilesystem(BaseModel):
    """Disk filesystem usage collected from node exporter."""
    mountpoint: str
    device: Optional[str] = None
    fstype: Optional[str] = None
    total: Optional[int] = None
    free: Optional[int] = None
    total_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
    used_percent: Optional[float] = None


class NodeExporterMetrics(BaseModel):
    """Host-level metrics collected from node exporter through VictoriaMetrics."""
    matched: bool = False
    matched_label: Optional[str] = None
    matched_value: Optional[str] = None
    cpu_usage_percent: Optional[float] = None
    cpu_cores: Optional[int] = None
    memory_used_percent: Optional[float] = None
    memory_total_bytes: Optional[int] = None
    memory_available_bytes: Optional[int] = None
    disks: List[NodeExporterDiskFilesystem] = Field(default_factory=list)
    scrape_up: Optional[bool] = None
