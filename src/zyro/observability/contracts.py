"""Durable operational trace contracts; telemetry is not Memory or State."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from zyro.core.data import freeze, plain, redact, validate_text


class TraceStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class TraceRecord:
    trace_id: str
    event_type: str
    timestamp: datetime
    component: str
    operation: str
    status: TraceStatus
    request_id: str
    task_id: str
    correlation_id: str
    workflow_id: str | None = None
    agent_id: str | None = None
    instance_id: str | None = None
    message_id: str | None = None
    event_id: str | None = None
    tool_id: str | None = None
    model_id: str | None = None
    approval_id: str | None = None
    verification_id: str | None = None
    recovery_id: str | None = None
    duration_ms: float | None = None
    attempt: int | None = None
    error_classification: str | None = None
    resource_usage: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "trace_id",
            "event_type",
            "component",
            "operation",
            "request_id",
            "task_id",
            "correlation_id",
        ):
            object.__setattr__(self, name, validate_text(getattr(self, name), name, max_chars=512))
        for name in (
            "workflow_id",
            "agent_id",
            "instance_id",
            "message_id",
            "event_id",
            "tool_id",
            "model_id",
            "approval_id",
            "verification_id",
            "recovery_id",
            "error_classification",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_text(value, name, max_chars=512))
        if not isinstance(self.status, TraceStatus):
            raise ValueError("trace status must be a TraceStatus")
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ValueError("trace timestamp must be timezone-aware")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("trace duration cannot be negative")
        if self.attempt is not None and self.attempt < 0:
            raise ValueError("trace attempt cannot be negative")
        for name in ("resource_usage", "metadata"):
            value = redact(getattr(self, name))
            if not isinstance(value, Mapping):
                raise ValueError(f"{name} must be a mapping")
            encoded = json.dumps(plain(value), sort_keys=True, separators=(",", ":"))
            if len(encoded.encode()) > 16_384:
                raise ValueError(f"{name} exceeds telemetry size limit")
            object.__setattr__(self, name, freeze(value))


@dataclass(frozen=True, slots=True)
class TraceQuery:
    correlation_id: str | None = None
    request_id: str | None = None
    task_id: str | None = None
    workflow_id: str | None = None
    recovery_id: str | None = None
    event_type: str | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if all(
            value is None
            for value in (
                self.correlation_id,
                self.request_id,
                self.task_id,
                self.workflow_id,
                self.recovery_id,
                self.event_type,
            )
        ):
            raise ValueError("trace query must include at least one filter")
        if not 1 <= self.limit <= 500:
            raise ValueError("trace query limit must be between 1 and 500")


__all__ = ["TraceQuery", "TraceRecord", "TraceStatus"]
