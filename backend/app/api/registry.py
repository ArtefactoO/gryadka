"""
Addresses monitor API endpoints.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.auth import require_permission
from app.audit import audit_log
from app.models.registry import (
    CreateManualRegistryEntryRequest,
    RegistryAutoRow,
    RegistryCollectQueuedResponse,
    RegistryDashboardResponse,
    RegistryManualRow,
    RegistryMismatchSummary,
    RegistrySummary,
    UpdateManualRegistryEntryRequest,
)
from app.services.registry_collector import registry_collector
from app.services.registry_manual_service import registry_manual_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/addresses", tags=["addresses"])
legacy_router = APIRouter(prefix="/api/registry", tags=["registry"])


def _raise_if_unavailable(exc: Exception) -> None:
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Monitoring operation failed")


@router.get("", response_model=RegistryDashboardResponse)
@legacy_router.get("", response_model=RegistryDashboardResponse)
async def get_registry_payload():
    try:
        return await asyncio.to_thread(registry_collector.get_dashboard_payload)
    except Exception as exc:
        _raise_if_unavailable(exc)


@router.get("/summary", response_model=RegistrySummary)
@legacy_router.get("/summary", response_model=RegistrySummary)
async def get_registry_summary():
    try:
        payload = await asyncio.to_thread(registry_collector.get_dashboard_payload)
        return payload.summary
    except Exception as exc:
        _raise_if_unavailable(exc)


@router.get("/mismatches", response_model=RegistryMismatchSummary)
@legacy_router.get("/mismatches", response_model=RegistryMismatchSummary)
async def get_registry_mismatches():
    try:
        return await asyncio.to_thread(registry_collector.get_mismatch_summary)
    except Exception as exc:
        _raise_if_unavailable(exc)


@router.post("/collect", response_model=RegistryCollectQueuedResponse)
@legacy_router.post("/collect", response_model=RegistryCollectQueuedResponse)
async def collect_registry(
    request: Request,
    current_user: dict = Depends(require_permission("addresses.collect")),
):
    try:
        from app.services.tasks import collect_all_addresses_task

        collect_all_addresses_task.delay()
        audit_log(
            "addresses_collect_queued",
            user=current_user,
            request=request,
            permission="addresses.collect",
            target_type="addresses",
            target_name="addresses",
        )
        return RegistryCollectQueuedResponse(
            queued=True,
            message="Addresses collection queued",
        )
    except HTTPException:
        raise
    except Exception as exc:
        _raise_if_unavailable(exc)


@router.get("/manual", response_model=List[RegistryManualRow])
@legacy_router.get("/manual", response_model=List[RegistryManualRow])
async def get_manual_registry_entries():
    try:
        return await asyncio.to_thread(registry_manual_service.list_rows)
    except Exception as exc:
        _raise_if_unavailable(exc)


@router.post("/manual", response_model=RegistryManualRow)
@legacy_router.post("/manual", response_model=RegistryManualRow)
async def create_manual_registry_entry(
    payload: CreateManualRegistryEntryRequest,
    request: Request,
    current_user: dict = Depends(require_permission("addresses.write")),
):
    try:
        row = await asyncio.to_thread(
            registry_manual_service.create_row,
            payload,
            current_user.get("sub"),
        )
        audit_log(
            "addresses_manual_create",
            user=current_user,
            request=request,
            permission="addresses.write",
            target_type="addresses",
            target_name="manual",
            entry_id=row.id,
            dns_name=row.dns_name,
            ip_or_cidr=row.ip_or_cidr,
        )
        return row
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monitoring operation failed")
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Monitoring operation failed")
    except Exception as exc:
        _raise_if_unavailable(exc)


@router.patch("/manual/{entry_id}", response_model=RegistryManualRow)
@legacy_router.patch("/manual/{entry_id}", response_model=RegistryManualRow)
async def update_manual_registry_entry(
    entry_id: int,
    payload: UpdateManualRegistryEntryRequest,
    request: Request,
    current_user: dict = Depends(require_permission("addresses.write")),
):
    try:
        row = await asyncio.to_thread(
            registry_manual_service.update_row,
            entry_id,
            payload,
            current_user.get("sub"),
        )
        audit_log(
            "addresses_manual_update",
            user=current_user,
            request=request,
            permission="addresses.write",
            target_type="addresses",
            target_name="manual",
            entry_id=row.id,
            dns_name=row.dns_name,
            ip_or_cidr=row.ip_or_cidr,
        )
        return row
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Manual addresses entry {entry_id} not found",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monitoring operation failed")
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Monitoring operation failed")
    except Exception as exc:
        _raise_if_unavailable(exc)


@router.delete("/manual/{entry_id}")
@legacy_router.delete("/manual/{entry_id}")
async def delete_manual_registry_entry(
    entry_id: int,
    request: Request,
    current_user: dict = Depends(require_permission("addresses.write")),
):
    try:
        deleted = await asyncio.to_thread(registry_manual_service.delete_row, entry_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Manual addresses entry {entry_id} not found",
            )
        audit_log(
            "addresses_manual_delete",
            user=current_user,
            request=request,
            permission="addresses.write",
            target_type="addresses",
            target_name="manual",
            entry_id=entry_id,
        )
        return {"deleted": True}
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Monitoring operation failed")
    except Exception as exc:
        _raise_if_unavailable(exc)
