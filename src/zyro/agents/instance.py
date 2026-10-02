"""Runtime state for one execution of an agent definition."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from zyro.core.errors import ErrorInfo, InvalidAgentError, InvalidAgentTransition


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AgentInstanceStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class ResourceAccounting:
    """Phase 2 accounting placeholder; enforcement belongs to a later phase."""

    input_units: int = 0
    output_units: int = 0


@dataclass(slots=True)
class AgentInstance:
    """A single runtime execution of an AgentDefinition."""

    instance_id: str
    agent_id: str
    task_id: str
    request_id: str
    correlation_id: str
    status: AgentInstanceStatus = field(default=AgentInstanceStatus.PENDING, init=False)
    created_at: datetime = field(default_factory=_utc_now, init=False)
    started_at: datetime | None = field(default=None, init=False)
    completed_at: datetime | None = field(default=None, init=False)
    result: Any | None = field(default=None, init=False)
    error: ErrorInfo | None = field(default=None, init=False)
    resources: ResourceAccounting = field(default_factory=ResourceAccounting, init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "instance_id",
            "agent_id",
            "task_id",
            "request_id",
            "correlation_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidAgentError(f"{field_name} must be a non-empty string")
            setattr(self, field_name, value.strip())

    def start(self) -> None:
        if self.status is not AgentInstanceStatus.PENDING:
            raise InvalidAgentTransition("only a pending agent instance can start")
        self.status = AgentInstanceStatus.RUNNING
        self.started_at = _utc_now()

    def succeed(self, result: Any) -> None:
        if self.status is not AgentInstanceStatus.RUNNING:
            raise InvalidAgentTransition("only a running agent instance can succeed")
        self.result = result
        self.status = AgentInstanceStatus.SUCCEEDED
        self.completed_at = _utc_now()

    def fail(self, error: ErrorInfo) -> None:
        if self.status is not AgentInstanceStatus.RUNNING:
            raise InvalidAgentTransition("only a running agent instance can fail")
        self.error = error
        self.status = AgentInstanceStatus.FAILED
        self.completed_at = _utc_now()

    def cancel(self) -> None:
        if self.status not in {AgentInstanceStatus.PENDING, AgentInstanceStatus.RUNNING}:
            raise InvalidAgentTransition("only a non-final agent instance can be cancelled")
        self.status = AgentInstanceStatus.CANCELLED
        self.completed_at = _utc_now()
