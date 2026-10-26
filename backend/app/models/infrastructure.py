"""Resource provenance shared by database and broker monitors."""
from typing import Any, Literal
from pydantic import BaseModel, Field


class InfrastructureMetrics(BaseModel):
    kind: Literal['kubernetes', 'vm', 'host', 'docker', 'unknown', 'none'] = 'unknown'
    state: Literal['available', 'unavailable', 'disabled'] = 'unavailable'
    source: str | None = None
    reason: str | None = None
    reason_code: str | None = None
    namespace: str | None = None
    pods: list[dict[str, Any]] = Field(default_factory=list)
    resources: dict[str, Any] | None = None
