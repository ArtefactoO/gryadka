"""Shared structured logging helpers for monitor collectors."""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

DEFAULT_INFO_OPERATIONS = {"replSetStepDown"}


def log_monitor_request(
    logger: logging.Logger,
    monitor_type: str,
    operation: str,
    *,
    host: Optional[str] = None,
    endpoint: Optional[str] = None,
    database: Optional[str] = None,
    info_operations: Optional[Iterable[str]] = None,
    level: Optional[int] = None,
    **details: Any,
) -> None:
    """Log a low-level monitor request.

    Regular collector reads are DEBUG by default. Explicitly important
    operations, such as admin actions, stay INFO.
    """
    info_set = set(info_operations or DEFAULT_INFO_OPERATIONS)
    log_level = level if level is not None else logging.INFO if operation in info_set else logging.DEBUG
    location = host or endpoint or "-"
    extra: dict[str, Any] = {
        "event_type": "monitor_request",
        "monitor_type": monitor_type,
        "monitor_operation": operation,
        "monitor_host": host,
        "monitor_endpoint": endpoint,
        "monitor_database": database,
    }
    if details:
        extra["monitor_details"] = details

    logger.log(log_level, "%s request: %s %s", monitor_type, location, operation, extra=extra)


def log_collector_progress(
    logger: logging.Logger,
    monitor_type: str,
    target_name: str,
    action: str,
    *,
    level: int = logging.DEBUG,
    **details: Any,
) -> None:
    """Log collector progress without flooding INFO logs."""
    extra: dict[str, Any] = {
        "event_type": "collector_progress",
        "monitor_type": monitor_type,
        "target_name": target_name,
        "collector_action": action,
    }
    if details:
        extra["collector_details"] = details

    logger.log(level, "%s collector %s: %s", monitor_type, action, target_name, extra=extra)


def log_collector_summary(
    logger: logging.Logger,
    monitor_type: str,
    action: str,
    *,
    count: Optional[int] = None,
    targets: Optional[Iterable[str]] = None,
    level: int = logging.DEBUG,
    **details: Any,
) -> None:
    """Log one concise monitor summary without flooding INFO logs."""
    target_list = list(targets or [])
    extra: dict[str, Any] = {
        "event_type": "collector_summary",
        "monitor_type": monitor_type,
        "collector_action": action,
        "target_count": count if count is not None else len(target_list),
        "targets": target_list,
    }
    if details:
        extra["collector_details"] = details

    logger.log(
        level,
        "%s collector %s: %s targets",
        monitor_type,
        action,
        extra["target_count"],
        extra=extra,
    )
