"""Minimal structured logging with ZYRO trace correlation fields."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, TextIO

TRACE_FIELDS = (
    "request_id",
    "task_id",
    "workflow_id",
    "agent_id",
    "instance_id",
    "correlation_id",
)


@dataclass(frozen=True, slots=True)
class LogContext:
    """Identifiers that correlate work without carrying payloads or secrets."""

    request_id: str | None = None
    task_id: str | None = None
    workflow_id: str | None = None
    agent_id: str | None = None
    instance_id: str | None = None
    correlation_id: str | None = None

    def fields(self) -> dict[str, str]:
        """Return only populated correlation identifiers."""
        return {key: value for key, value in asdict(self).items() if value is not None}


class JsonFormatter(logging.Formatter):
    """Render predictable JSON records without configuration or environment data."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in TRACE_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> logging.Logger:
    """Configure and return the isolated root logger for the ZYRO package."""
    logger = logging.getLogger("zyro")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(
    name: str,
    context: LogContext | Mapping[str, str] | None = None,
) -> logging.LoggerAdapter[logging.Logger]:
    """Create a logger adapter containing only approved correlation fields."""
    if isinstance(context, LogContext):
        fields = context.fields()
    else:
        supplied = {} if context is None else context
        fields = {key: value for key, value in supplied.items() if key in TRACE_FIELDS}
    return logging.LoggerAdapter(logging.getLogger(f"zyro.{name}"), fields)
