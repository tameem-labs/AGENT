"""Failure classification and deterministic Recovery decision contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from zyro.core.data import validate_text
from zyro.core.errors import ErrorInfo


class FailureClass(StrEnum):
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"
    APPROVAL_FAILURE = "APPROVAL_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    TOOL_FAILURE = "TOOL_FAILURE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    TIMEOUT = "TIMEOUT"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"
    RESOURCE_EXHAUSTION = "RESOURCE_EXHAUSTION"
    PROCESS_CRASH = "PROCESS_CRASH"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"
    EXTERNAL_SIDE_EFFECT_UNCERTAIN = "EXTERNAL_SIDE_EFFECT_UNCERTAIN"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"


class SideEffectState(StrEnum):
    NONE = "NONE"
    NOT_STARTED = "NOT_STARTED"
    CONFIRMED = "CONFIRMED"
    UNCERTAIN = "UNCERTAIN"


class RecoveryAction(StrEnum):
    RETRY = "RETRY"
    FALLBACK = "FALLBACK"
    RESUME = "RESUME"
    WAIT = "WAIT"
    ESCALATE = "ESCALATE"
    STOP = "STOP"
    MARK_UNCERTAIN = "MARK_UNCERTAIN"


@dataclass(frozen=True, slots=True)
class FailureIdentity:
    request_id: str
    task_id: str
    correlation_id: str
    workflow_id: str | None = None
    agent_id: str | None = None
    instance_id: str | None = None
    tool_id: str | None = None
    model_id: str | None = None
    approval_id: str | None = None
    verification_id: str | None = None
    message_id: str | None = None
    event_id: str | None = None
    component_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("request_id", "task_id", "correlation_id"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        for name in (
            "workflow_id",
            "agent_id",
            "instance_id",
            "tool_id",
            "model_id",
            "approval_id",
            "verification_id",
            "message_id",
            "event_id",
            "component_id",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_text(value, name))


@dataclass(frozen=True, slots=True)
class FailureRecord:
    failure_id: str
    classification: FailureClass
    identity: FailureIdentity
    error: ErrorInfo
    occurred_at: datetime
    retryable: bool
    side_effect: SideEffectState = SideEffectState.NONE

    def __post_init__(self) -> None:
        object.__setattr__(self, "failure_id", validate_text(self.failure_id, "failure_id"))
        if not isinstance(self.classification, FailureClass):
            raise ValueError("classification must be a FailureClass")
        if not isinstance(self.side_effect, SideEffectState):
            raise ValueError("side_effect must be a SideEffectState")
        if self.occurred_at.tzinfo is None:
            raise ValueError("failure time must be timezone-aware")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be a boolean")


@dataclass(frozen=True, slots=True)
class RecoveryRequest:
    recovery_id: str
    operation_id: str
    failure: FailureRecord
    attempt_count: int
    max_attempts: int
    idempotent: bool
    resource_available: bool
    hard_resource_limit: bool
    fallback_available: bool
    reconciliation_available: bool
    current_task_state: str
    operation_revision: int = 1

    def __post_init__(self) -> None:
        for name in ("recovery_id", "operation_id", "current_task_state"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        if not 0 <= self.attempt_count <= self.max_attempts <= 100:
            raise ValueError("recovery attempt bounds are invalid")
        if self.operation_revision < 1:
            raise ValueError("operation revision must be positive")


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    recovery_id: str
    operation_id: str
    operation_revision: int
    action: RecoveryAction
    decided_at: datetime
    reason_code: str
    reason: str
    next_attempt: int
    backoff_seconds: float = 0.0
    requires_reconciliation: bool = False

    def __post_init__(self) -> None:
        if self.decided_at.tzinfo is None:
            raise ValueError("recovery decision time must be timezone-aware")
        if self.next_attempt < 0 or self.backoff_seconds < 0:
            raise ValueError("recovery counters cannot be negative")


class RecoverableOperationStatus(StrEnum):
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    RETRY_WAIT = "RETRY_WAIT"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    STOPPED = "STOPPED"

    @property
    def final(self) -> bool:
        return self in {
            RecoverableOperationStatus.SUCCEEDED,
            RecoverableOperationStatus.FAILED,
            RecoverableOperationStatus.UNCERTAIN,
            RecoverableOperationStatus.STOPPED,
        }


@dataclass(frozen=True, slots=True)
class RecoverableOperation:
    operation_id: str
    identity: FailureIdentity
    status: RecoverableOperationStatus
    attempt_count: int
    max_attempts: int
    idempotent: bool
    side_effect: SideEffectState
    revision: int
    updated_at: datetime


__all__ = [
    "FailureClass",
    "FailureIdentity",
    "FailureRecord",
    "RecoverableOperation",
    "RecoverableOperationStatus",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryRequest",
    "SideEffectState",
]
