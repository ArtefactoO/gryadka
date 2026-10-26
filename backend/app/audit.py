"""Structured audit logging helpers for privileged API actions."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request

logger = logging.getLogger("app.audit")


def _user_name(user: Optional[dict[str, Any]]) -> Optional[str]:
    if not user:
        return None
    value = user.get("sub") or user.get("username")
    return str(value) if value else None


def audit_log(
    action: str,
    *,
    user: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
    permission: Optional[str] = None,
    target_type: Optional[str] = None,
    target_name: Optional[str] = None,
    status: str = "success",
    **details: Any,
) -> None:
    extra: dict[str, Any] = {
        "event_type": "audit",
        "audit_action": action,
        "audit_status": status,
        "username": _user_name(user),
        "auth_provider": user.get("auth_provider") if user else None,
        "roles": user.get("roles", []) if user else [],
        "permission": permission,
        "target_type": target_type,
        "target_name": target_name,
    }
    if request:
        extra.update(
            {
                "client_addr": request.client.host if request.client else None,
                "method": request.method,
                "path": request.url.path,
            }
        )
    for key, value in details.items():
        if value is not None:
            extra[key] = value

    logger.info("API audit event", extra=extra)
