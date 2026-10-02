"""Narrow operational observer for structured trace emission."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from zyro.observability.contracts import TraceRecord, TraceStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TraceContext:
    request_id: str
    task_id: str
    correlation_id: str
    workflow_id: str | None = None
    agent_id: str | None = None
    instance_id: str | None = None


class TraceWriter(Protocol):
    def append(self, record: TraceRecord) -> bool: ...


class OperationalObserver:
    def __init__(
        self,
        writer: TraceWriter,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._writer = writer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def record(
        self,
        event_type: str,
        component: str,
        operation: str,
        status: TraceStatus,
        context: TraceContext,
        **fields: Any,
    ) -> TraceRecord:
        record = TraceRecord(
            trace_id=self._id_factory(),
            event_type=event_type,
            timestamp=self._clock(),
            component=component,
            operation=operation,
            status=status,
            request_id=context.request_id,
            task_id=context.task_id,
            workflow_id=context.workflow_id,
            agent_id=context.agent_id,
            instance_id=context.instance_id,
            correlation_id=context.correlation_id,
            message_id=fields.get("message_id"),
            event_id=fields.get("event_id"),
            tool_id=fields.get("tool_id"),
            model_id=fields.get("model_id"),
            approval_id=fields.get("approval_id"),
            verification_id=fields.get("verification_id"),
            recovery_id=fields.get("recovery_id"),
            duration_ms=fields.get("duration_ms"),
            attempt=fields.get("attempt"),
            error_classification=fields.get("error_classification"),
            resource_usage=fields.get("resource_usage", {}),
            metadata=fields.get("metadata", {}),
        )
        self._writer.append(record)
        return record


class Observer(Protocol):
    def record(
        self,
        event_type: str,
        component: str,
        operation: str,
        status: TraceStatus,
        context: TraceContext,
        **fields: Any,
    ) -> TraceRecord: ...


__all__ = ["Observer", "OperationalObserver", "TraceContext", "TraceWriter"]
