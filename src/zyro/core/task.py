"""Task contract and guarded lifecycle for bounded ZYRO work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from zyro.core.errors import ErrorInfo, InvalidTaskError, InvalidTaskTransition
from zyro.core.verification import VerificationEvidence


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TaskStatus(StrEnum):
    """Canonical internal task lifecycle states."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    DONE = "DONE"
    FAILED = "FAILED"
    RETRY = "RETRY"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class VerificationStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    status: VerificationStatus = VerificationStatus.NOT_RUN
    summary: str = "Verification has not run."
    scope: str | None = None
    verified_at: datetime | None = None
    verification_id: str | None = None
    execution_id: str | None = None
    verifier_id: str | None = None
    evidence: tuple[VerificationEvidence, ...] = ()


_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_FOR_APPROVAL,
            TaskStatus.VERIFYING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING_FOR_APPROVAL: frozenset(
        {TaskStatus.RETRY, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.VERIFYING: frozenset(
        {TaskStatus.VERIFIED, TaskStatus.FAILED, TaskStatus.RETRY, TaskStatus.CANCELLED}
    ),
    TaskStatus.VERIFIED: frozenset({TaskStatus.DONE, TaskStatus.CANCELLED}),
    TaskStatus.FAILED: frozenset({TaskStatus.RETRY, TaskStatus.CANCELLED}),
    TaskStatus.RETRY: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


@dataclass(slots=True)
class Task:
    """A bounded unit of work with explicit execution and verification state."""

    task_id: str
    request_id: str
    correlation_id: str
    goal: str
    owner: str
    priority: TaskPriority = TaskPriority.NORMAL
    dependencies: tuple[str, ...] = ()
    assigned_agent_id: str | None = None
    max_attempts: int = 1
    resource_budget: dict[str, int] = field(default_factory=dict)
    verification_plan: str | None = None
    status: TaskStatus = field(default=TaskStatus.PENDING, init=False)
    pending_approval_id: str | None = field(default=None, init=False)
    attempt_count: int = field(default=0, init=False)
    result: Any | None = field(default=None, init=False)
    error: ErrorInfo | None = field(default=None, init=False)
    verification: VerificationRecord = field(default_factory=VerificationRecord, init=False)
    created_at: datetime = field(default_factory=_utc_now, init=False)
    started_at: datetime | None = field(default=None, init=False)
    updated_at: datetime = field(default_factory=_utc_now, init=False)
    completed_at: datetime | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        for field_name in ("task_id", "request_id", "correlation_id", "goal", "owner"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidTaskError(f"{field_name} must be a non-empty string")
            setattr(self, field_name, value.strip())
        if self.max_attempts < 1:
            raise InvalidTaskError("max_attempts must be at least 1")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise InvalidTaskError("dependencies must not contain duplicates")
        if self.task_id in self.dependencies:
            raise InvalidTaskError("a task cannot depend on itself")
        if any(value < 0 for value in self.resource_budget.values()):
            raise InvalidTaskError("resource budget values cannot be negative")

    @property
    def is_final(self) -> bool:
        return self.status in {TaskStatus.DONE, TaskStatus.CANCELLED} or (
            self.status is TaskStatus.FAILED and not self.can_retry
        )

    @property
    def can_retry(self) -> bool:
        return (
            self.status is TaskStatus.FAILED
            and self.attempt_count < self.max_attempts
            and self.error is not None
            and self.error.retryable
        )

    def assign_agent(self, agent_id: str) -> None:
        if self.status not in {TaskStatus.PENDING, TaskStatus.RETRY}:
            raise InvalidTaskTransition("an agent can only be assigned before an attempt starts")
        if not agent_id.strip():
            raise InvalidTaskError("assigned agent id must not be empty")
        self.assigned_agent_id = agent_id.strip()
        self._touch()

    def start(self) -> None:
        if self.attempt_count >= self.max_attempts:
            raise InvalidTaskTransition("task attempt limit has been reached")
        self._transition(TaskStatus.RUNNING)
        self.attempt_count += 1
        if self.started_at is None:
            self.started_at = self.updated_at
        self.error = None

    def record_execution_success(self, result: Any) -> None:
        self.result = result
        self.error = None
        self.pending_approval_id = None
        self._transition(TaskStatus.VERIFYING)

    def record_execution_unknown(self, result: Any, error: ErrorInfo) -> None:
        self.result = result
        self.error = error
        self.pending_approval_id = None
        self._transition(TaskStatus.VERIFYING)

    def wait_for_approval(self, approval_id: str) -> None:
        if not approval_id.strip():
            raise InvalidTaskError("approval_id must be a non-empty string")
        self.pending_approval_id = approval_id.strip()
        self._transition(TaskStatus.WAITING_FOR_APPROVAL)

    def resume_after_approval(self) -> None:
        if self.status is not TaskStatus.WAITING_FOR_APPROVAL:
            raise InvalidTaskTransition("only a task waiting for approval can resume")
        # Approval waiting pauses the current attempt; it is not a retryable execution failure.
        self.attempt_count -= 1
        self._transition(TaskStatus.RETRY)

    def fail(self, error: ErrorInfo) -> None:
        self.error = error
        self._transition(TaskStatus.FAILED)
        if not self.can_retry:
            self.completed_at = self.updated_at

    def prepare_retry(self) -> None:
        if not self.can_retry:
            raise InvalidTaskTransition(
                "task failure is not retryable or attempt limit was reached"
            )
        self._transition(TaskStatus.RETRY)
        self.result = None
        self.verification = VerificationRecord()

    def mark_verified(
        self,
        summary: str,
        scope: str,
        *,
        verification_id: str | None = None,
        execution_id: str | None = None,
        verifier_id: str | None = None,
        evidence: tuple[VerificationEvidence, ...] = (),
        verified_at: datetime | None = None,
    ) -> None:
        self.verification = VerificationRecord(
            status=VerificationStatus.VERIFIED,
            summary=summary,
            scope=scope,
            verified_at=verified_at or _utc_now(),
            verification_id=verification_id,
            execution_id=execution_id,
            verifier_id=verifier_id,
            evidence=evidence,
        )
        self._transition(TaskStatus.VERIFIED)

    def mark_verification_failed(
        self,
        error: ErrorInfo,
        scope: str,
        *,
        verification_id: str | None = None,
        execution_id: str | None = None,
        verifier_id: str | None = None,
        evidence: tuple[VerificationEvidence, ...] = (),
        verified_at: datetime | None = None,
    ) -> None:
        self.verification = VerificationRecord(
            status=VerificationStatus.FAILED,
            summary=error.message,
            scope=scope,
            verified_at=verified_at or _utc_now(),
            verification_id=verification_id,
            execution_id=execution_id,
            verifier_id=verifier_id,
            evidence=evidence,
        )
        self.fail(error)

    def retry_after_verification_failure(self, error: ErrorInfo, scope: str) -> None:
        if self.status is not TaskStatus.VERIFYING:
            raise InvalidTaskTransition("verification retry requires a verifying task")
        if not error.retryable or self.attempt_count >= self.max_attempts:
            raise InvalidTaskTransition("verification failure cannot be retried")
        self.error = error
        self.verification = VerificationRecord(
            status=VerificationStatus.FAILED,
            summary=error.message,
            scope=scope,
            verified_at=_utc_now(),
        )
        self._transition(TaskStatus.RETRY)
        self.result = None

    def mark_verification_unavailable(
        self,
        summary: str,
        *,
        verification_id: str | None = None,
        execution_id: str | None = None,
        verifier_id: str | None = None,
        evidence: tuple[VerificationEvidence, ...] = (),
        verified_at: datetime | None = None,
    ) -> None:
        if self.status is not TaskStatus.VERIFYING:
            raise InvalidTaskTransition("verification can only be unavailable while verifying")
        self.verification = VerificationRecord(
            status=VerificationStatus.UNAVAILABLE,
            summary=summary,
            verified_at=verified_at,
            verification_id=verification_id,
            execution_id=execution_id,
            verifier_id=verifier_id,
            evidence=evidence,
        )
        self._touch()

    def complete(self) -> None:
        self._transition(TaskStatus.DONE)
        self.completed_at = self.updated_at

    def cancel(self, reason: str = "Task cancelled.") -> None:
        if self.is_final:
            raise InvalidTaskTransition(f"cannot cancel final task in {self.status} state")
        self.error = ErrorInfo(
            code="task_cancelled",
            message=reason,
            error_type="Cancellation",
        )
        self._transition(TaskStatus.CANCELLED)
        self.completed_at = self.updated_at

    def _transition(self, target: TaskStatus) -> None:
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTaskTransition(f"invalid task transition: {self.status} -> {target}")
        self.status = target
        self._touch()

    def _touch(self) -> None:
        self.updated_at = _utc_now()
