import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any
from app.redaction import environment_secrets, redact_payload

_RESERVED_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__.keys())
_DEFAULT_LOG_LEVEL = "INFO"


def _resolve_log_level() -> int:
    raw_level = str(os.getenv("LOG_LEVEL", _DEFAULT_LOG_LEVEL) or _DEFAULT_LOG_LEVEL).strip().upper()
    aliases = {
        "WARN": "WARNING",
        "FATAL": "CRITICAL",
    }
    normalized = aliases.get(raw_level, raw_level)
    level = logging.getLevelName(normalized)
    return level if isinstance(level, int) else logging.INFO


def _detect_process_role() -> str:
    argv = " ".join(sys.argv).lower()
    if "celery" in argv and " beat" in argv:
        return "beat"
    if "celery" in argv and " worker" in argv:
        return "worker"
    if "uvicorn" in argv:
        return "app"
    return "app"


class JsonLogFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self._service = service
        self._process_role = _detect_process_role()
        self._secrets = environment_secrets()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "process_role": self._process_role,
            "message": record.getMessage(),
        }

        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) >= 5:
            payload["client_addr"] = record.args[0]
            payload["method"] = record.args[1]
            payload["path"] = record.args[2]
            payload["http_version"] = record.args[3]
            payload["status_code"] = record.args[4]

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_FIELDS:
                continue
            if key in payload:
                continue
            payload[key] = value

        return json.dumps(redact_payload(payload, self._secrets), ensure_ascii=True, default=str)


def configure_logging(service: str = "gryadka") -> None:
    log_level = _resolve_log_level()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter(service=service))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    for logger_name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "celery",
        "celery.app.trace",
        "celery.worker",
        "celery.beat",
        "kombu",
    ):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

    for logger_name in (
        "celery.app.trace",
        "celery.apps.worker",
        "celery.beat",
        "celery.worker.control",
        "celery.worker.consumer.connection",
        "celery.worker.consumer.mingle",
        "celery.worker.strategy",
        "elastic_transport",
        "elastic_transport.transport",
        "kafka",
        "kafka.conn",
        "uvicorn.access",
    ):
        logging.getLogger(logger_name).setLevel(max(log_level, logging.WARNING))
