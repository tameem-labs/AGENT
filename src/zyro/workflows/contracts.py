"""Small durable workflow contracts for local ZYRO orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from zyro.core.data import validate_text


class WorkflowStatus(StrEnum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    VERIFYING = "VERIFYING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TriggerKind(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    SCHEDULED = "SCHEDULED"
    RECURRING = "RECURRING"
    EVENT = "EVENT"


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    step_id: str
    name: str
    capability: str
    agent_id: str
    dependencies: tuple[str, ...] = ()
    max_attempts: int = 1
    requires_approval: bool = False

    def __post_init__(self) -> None:
        for name in ("step_id", "name", "capability", "agent_id"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        object.__setattr__(
            self,
            "dependencies",
            tuple(validate_text(item, "dependency") for item in self.dependencies),
        )
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("step max_attempts must be between 1 and 10")


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    workflow_id: str
    request_id: str
    correlation_id: str
    owner_principal_id: str
    goal: str
    steps: tuple[WorkflowStep, ...]
    trigger: TriggerKind = TriggerKind.IMMEDIATE
    scheduled_for: datetime | None = None
    recurrence_seconds: int | None = None
    event_type: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("workflow_id", "request_id", "correlation_id", "owner_principal_id", "goal"):
            object.__setattr__(
                self, name, validate_text(getattr(self, name), name, max_chars=8_000)
            )
        if not self.steps:
            raise ValueError("workflow requires at least one step")
        ids = {step.step_id for step in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("workflow step identities must be unique")
        if any(set(step.dependencies) - ids for step in self.steps):
            raise ValueError("workflow dependency is not a workflow step")
        self._assert_acyclic()
        if self.trigger is TriggerKind.SCHEDULED and self.scheduled_for is None:
            raise ValueError("scheduled workflow requires scheduled_for")
        if self.trigger is TriggerKind.RECURRING and (
            self.recurrence_seconds is None or self.recurrence_seconds < 60
        ):
            raise ValueError("recurring workflow requires recurrence of at least 60 seconds")
        if self.trigger is TriggerKind.EVENT and not self.event_type:
            raise ValueError("event workflow requires event_type")
        if self.scheduled_for is not None and self.scheduled_for.tzinfo is None:
            raise ValueError("scheduled_for must be timezone-aware")

    def _assert_acyclic(self) -> None:
        dependencies = {item.step_id: set(item.dependencies) for item in self.steps}
        pending = dict(dependencies)
        while pending:
            ready = {key for key, values in pending.items() if not values}
            if not ready:
                raise ValueError("workflow dependencies must be acyclic")
            for key in ready:
                pending.pop(key)
            for values in pending.values():
                values.difference_update(ready)


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    definition: WorkflowDefinition
    status: WorkflowStatus
    revision: int
    step_statuses: dict[str, StepStatus]
    step_attempts: dict[str, int]
    created_at: datetime
    updated_at: datetime
    waiting_reason: str | None = None
    error_code: str | None = None
    next_run_at: datetime | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)


__all__ = [
    "StepStatus",
    "TriggerKind",
    "WorkflowDefinition",
    "WorkflowSnapshot",
    "WorkflowStatus",
    "WorkflowStep",
]
