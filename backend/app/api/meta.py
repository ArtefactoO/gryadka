"""Application metadata endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.version import get_app_changelog, get_app_version

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/app")
async def get_app_meta():
    """Return public application metadata for the UI."""
    return {
        "version": get_app_version(),
    }


@router.get("/changelog")
async def get_changelog():
    """Return public application changelog for the UI."""
    return {
        "content": get_app_changelog(),
    }
