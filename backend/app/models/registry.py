"""
Data models for Registry monitor payloads and CRUD.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.cluster import MonitoringTargetSummary


class RegistrySourceReference(BaseModel):
    repo_name: str
    repo_url: Optional[str] = None
    source_file_url: Optional[str] = None
    repo_ref: Optional[str] = None
    repo_commit: Optional[str] = None
    file_path: str
    object_key: str
    object_type: Optional[str] = None
    extractor_type: Optional[str] = None
    source_object_id: Optional[int] = None
    source_line_start: Optional[int] = None
    source_line_end: Optional[int] = None


class RegistryRepoSummary(BaseModel):
    repo_name: str
    repo_ref: Optional[str] = None
    repo_commit: Optional[str] = None
    status: str
    last_synced_at: Optional[datetime] = None
    error: Optional[str] = None


class RegistrySummary(BaseModel):
    auto_rows_count: int = 0
    manual_rows_count: int = 0
    linked_rows_count: int = 0
    total_source_objects: int = 0
    total_attributes: int = 0
    total_linked_rows: int = 0
    total_manual_rows: int = 0
    last_sync_status: str = "unknown"
    last_sync_at: Optional[datetime] = None
    last_successful_sync_at: Optional[datetime] = None
    refresh_interval_s: int = 1200
    repos: List[RegistryRepoSummary] = Field(default_factory=list)
    views: List[Dict[str, Any]] = Field(default_factory=list)


class RegistryDnsValidation(BaseModel):
    status: str = "skipped"
    resolved_ips: List[str] = Field(default_factory=list)
    checked_at: Optional[datetime] = None
    message: Optional[str] = None


class RegistryAutoRow(BaseModel):
    ip: str
    address_scope: str = "external"
    service_name: Optional[str] = None
    dns_name: Optional[str] = None
    dns_names: List[str] = Field(default_factory=list)
    dns_validation: Optional[RegistryDnsValidation] = None
    previous_ips: List[str] = Field(default_factory=list)
    ip_changed_at: Optional[datetime] = None
    protocol: Optional[str] = None
    port: Optional[int] = None
    origin: str = "auto_gitlab"
    environment: Optional[str] = None
    component: Optional[str] = None
    source_references: List[RegistrySourceReference] = Field(default_factory=list)


class RegistryManualRow(BaseModel):
    id: int
    label: str
    ip: str
    ip_or_cidr: str
    address_scope: str = "external"
    service_name: str
    dns_name: Optional[str] = None
    dns_names: List[str] = Field(default_factory=list)
    dns_validation: Optional[RegistryDnsValidation] = None
    previous_ips: List[str] = Field(default_factory=list)
    ip_changed_at: Optional[datetime] = None
    protocol: Optional[str] = None
    port: Optional[int] = None
    comment: str = ""
    owner: Optional[str] = None
    created_by: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    origin: str = "manual"
    created_at: datetime
    updated_at: datetime
    is_active: bool = True


class CreateManualRegistryEntryRequest(BaseModel):
    label: str = ""
    service_name: str
    ip_or_cidr: str
    address_scope: Optional[str] = None
    dns_name: Optional[str] = None
    protocol: Optional[str] = None
    port: Optional[int] = None
    comment: str = ""
    owner: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    is_active: bool = True


class UpdateManualRegistryEntryRequest(BaseModel):
    label: Optional[str] = None
    service_name: Optional[str] = None
    ip_or_cidr: Optional[str] = None
    address_scope: Optional[str] = None
    dns_name: Optional[str] = None
    protocol: Optional[str] = None
    port: Optional[int] = None
    comment: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None


class RegistryDashboardResponse(BaseModel):
    target: MonitoringTargetSummary
    status: str
    last_update: Optional[datetime] = None
    summary: RegistrySummary
    auto_rows: List[RegistryAutoRow] = Field(default_factory=list)
    manual_rows: List[RegistryManualRow] = Field(default_factory=list)


class RegistryCollectResponse(RegistryDashboardResponse):
    queued: bool = False
    message: Optional[str] = None


class RegistryCollectQueuedResponse(BaseModel):
    queued: bool = True
    message: Optional[str] = None


class RegistryMismatchSummary(BaseModel):
    total_count: int = 0
    auto_count: int = 0
    manual_count: int = 0


class RegistryManualListResponse(BaseModel):
    items: List[RegistryManualRow] = Field(default_factory=list)


# Backward-compatible aliases for older import sites.
ManualRegistryEntryCreate = CreateManualRegistryEntryRequest
ManualRegistryEntryUpdate = UpdateManualRegistryEntryRequest
RegistryPayload = RegistryDashboardResponse
RegistryRow = RegistryAutoRow
