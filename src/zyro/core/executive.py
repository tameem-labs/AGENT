"""Canonical ZYRO Executive orchestration boundary for Phase 2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from zyro.core.errors import ErrorInfo, InvalidRequestError
from zyro.core.logging import LogContext, get_logger
from zyro.core.task import Task, TaskPriority, TaskStatus, VerificationRecord
from zyro.execution.verification import VerificationOutcome, Verifier
from zyro.observability.contracts import TraceStatus
from zyro.observability.service import Observer, TraceContext
from zyro.runtime.agent_runtime import AgentRuntime, RuntimeExecution


@dataclass(frozen=True, slots=True)
class UserRequest:
    goal: str
    requester: str
    agent_id: str
    request_id: str | None = None
    correlation_id: str | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    max_attempts: int = 1
    workflow_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("goal", "requester", "agent_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidRequestError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        for field_name in ("request_id", "correlation_id", "workflow_id"):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise InvalidRequestError(
                        f"{field_name} must be a non-empty string when supplied"
                    )
                object.__setattr__(self, field_name, value.strip())
        if not 1 <= self.max_attempts <= 10:
            raise InvalidRequestError("max_attempts must be between 1 and 10")


class ExecutiveOutcome(StrEnum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    SUCCEEDED_UNVERIFIED = "SUCCEEDED_UNVERIFIED"
    UNKNOWN_UNVERIFIED = "UNKNOWN_UNVERIFIED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ExecutiveResult:
    outcome: ExecutiveOutcome
    request_id: str
    task_id: str
    correlation_id: str
    task_status: TaskStatus
    result: Any | None
    error: ErrorInfo | None
    verification: VerificationRecord
    instance_id: str | None
    attempts: int


class ZyroExecutive:
    """One orchestration entry point; it is not an LLM or agent implementation."""

    def __init__(
        self,
        runtime: AgentRuntime,
        verifier: Verifier | None = None,
        id_factory: Callable[[], str] | None = None,
        observer: Observer | None = None,
    ) -> None:
        self._runtime = runtime
        self._verifier = verifier
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._observer = observer

    def handle(self, request: UserRequest) -> ExecutiveResult:
        """Accept one request and coordinate bounded execution and verification."""
        request_id = request.request_id or self._id_factory()
        correlation_id = request.correlation_id or self._id_factory()
        task = Task(
            task_id=self._id_factory(),
            request_id=request_id,
            correlation_id=correlation_id,
            goal=request.goal,
            owner=request.requester,
            priority=request.priority,
            max_attempts=request.max_attempts,
            verification_plan=(
                "injected verifier" if self._verifier is not None else "no verifier available"
            ),
            workflow_id=request.workflow_id,
        )
        logger = get_logger(
            "executive",
            LogContext(
                request_id=request_id,
                task_id=task.task_id,
                agent_id=request.agent_id,
                correlation_id=correlation_id,
            ),
        )
        logger.info("request accepted and task created")
        self._observe(task, request.agent_id, "REQUEST_RECEIVED", TraceStatus.STARTED)
        self._observe(task, request.agent_id, "TASK_CREATED", TraceStatus.SUCCEEDED)

        while True:
            last_runtime_result = self._runtime.execute(task, request.agent_id)
            execution = last_runtime_result.execution
            if task.status is TaskStatus.WAITING_FOR_APPROVAL:
                logger.info("task is waiting for explicit approval")
                return self._report(
                    task,
                    last_runtime_result,
                    ExecutiveOutcome.WAITING_FOR_APPROVAL,
                )
            if not execution.succeeded and not execution.outcome_unknown:
                if task.can_retry:
                    task.prepare_retry()
                    logger.info("retrying task after retryable execution failure")
                    continue
                return self._report(task, last_runtime_result, ExecutiveOutcome.FAILED)

            if self._verifier is None:
                task.mark_verification_unavailable("No verifier was configured for this task.")
                logger.warning("execution outcome remains unverified")
                self._observe(
                    task,
                    request.agent_id,
                    "VERIFICATION_COMPLETED",
                    TraceStatus.UNKNOWN,
                    instance_id=(
                        None
                        if last_runtime_result.instance is None
                        else last_runtime_result.instance.instance_id
                    ),
                )
                return self._report(
                    task,
                    last_runtime_result,
                    (
                        ExecutiveOutcome.UNKNOWN_UNVERIFIED
                        if execution.outcome_unknown
                        else ExecutiveOutcome.SUCCEEDED_UNVERIFIED
                    ),
                )

            self._observe(
                task,
                request.agent_id,
                "VERIFICATION_STARTED",
                TraceStatus.STARTED,
                instance_id=(
                    None
                    if last_runtime_result.instance is None
                    else last_runtime_result.instance.instance_id
                ),
            )
            try:
                verification = self._verifier.verify(task, last_runtime_result)
            except Exception as error:
                failure = ErrorInfo(
                    code="verification_exception",
                    message=f"Verifier raised {type(error).__name__}.",
                    error_type=type(error).__name__,
                )
                task.mark_verification_failed(failure, "verifier_execution")
                logger.error("verifier raised an exception")
                self._observe(
                    task,
                    request.agent_id,
                    "VERIFICATION_COMPLETED",
                    TraceStatus.FAILED,
                    instance_id=(
                        None
                        if last_runtime_result.instance is None
                        else last_runtime_result.instance.instance_id
                    ),
                    error=failure,
                )
                return self._report(task, last_runtime_result, ExecutiveOutcome.FAILED)

            verification_status = (
                TraceStatus.SUCCEEDED
                if verification.outcome is VerificationOutcome.VERIFIED
                else (
                    TraceStatus.UNKNOWN
                    if verification.outcome
                    in {VerificationOutcome.UNAVAILABLE, VerificationOutcome.UNKNOWN}
                    else TraceStatus.FAILED
                )
            )
            self._observe(
                task,
                request.agent_id,
                "VERIFICATION_COMPLETED",
                verification_status,
                instance_id=(
                    None
                    if last_runtime_result.instance is None
                    else last_runtime_result.instance.instance_id
                ),
                verification_id=verification.verification_id,
                error=verification.error,
            )

            if verification.outcome is VerificationOutcome.VERIFIED:
                task.mark_verified(
                    verification.summary,
                    verification.scope or "unspecified",
                    verification_id=verification.verification_id,
                    execution_id=verification.execution_id,
                    verifier_id=verification.verifier_id,
                    evidence=verification.evidence,
                    verified_at=verification.verified_at,
                )
                task.complete()
                logger.info("task verified and completed")
                self._observe(
                    task,
                    request.agent_id,
                    "TASK_COMPLETED",
                    TraceStatus.SUCCEEDED,
                    verification_id=verification.verification_id,
                )
                return self._report(
                    task,
                    last_runtime_result,
                    ExecutiveOutcome.VERIFIED_SUCCESS,
                )
            if verification.outcome in {
                VerificationOutcome.UNAVAILABLE,
                VerificationOutcome.UNKNOWN,
            }:
                task.mark_verification_unavailable(
                    verification.summary,
                    verification_id=verification.verification_id,
                    execution_id=verification.execution_id,
                    verifier_id=verification.verifier_id,
                    evidence=verification.evidence,
                    verified_at=verification.verified_at,
                )
                return self._report(
                    task,
                    last_runtime_result,
                    (
                        ExecutiveOutcome.UNKNOWN_UNVERIFIED
                        if execution.outcome_unknown
                        else ExecutiveOutcome.SUCCEEDED_UNVERIFIED
                    ),
                )

            assert verification.error is not None
            scope = verification.scope or "unspecified"
            if verification.error.retryable and task.attempt_count < task.max_attempts:
                task.retry_after_verification_failure(verification.error, scope)
                logger.info("retrying task after retryable verification failure")
                continue
            task.mark_verification_failed(
                verification.error,
                scope,
                verification_id=verification.verification_id,
                execution_id=verification.execution_id,
                verifier_id=verification.verifier_id,
                evidence=verification.evidence,
                verified_at=verification.verified_at,
            )
            return self._report(task, last_runtime_result, ExecutiveOutcome.FAILED)

    def _observe(
        self,
        task: Task,
        agent_id: str,
        event_type: str,
        status: TraceStatus,
        *,
        instance_id: str | None = None,
        verification_id: str | None = None,
        error: ErrorInfo | None = None,
    ) -> None:
        if self._observer is None:
            return
        try:
            self._observer.record(
                event_type,
                "executive",
                "handle",
                status,
                TraceContext(
                    task.request_id,
                    task.task_id,
                    task.correlation_id,
                    agent_id=agent_id,
                    instance_id=instance_id,
                ),
                attempt=task.attempt_count,
                verification_id=verification_id,
                error_classification=None if error is None else error.error_type,
            )
        except Exception:
            return

    @staticmethod
    def _report(
        task: Task,
        runtime_result: RuntimeExecution,
        outcome: ExecutiveOutcome,
    ) -> ExecutiveResult:
        instance = runtime_result.instance
        return ExecutiveResult(
            outcome=outcome,
            request_id=task.request_id,
            task_id=task.task_id,
            correlation_id=task.correlation_id,
            task_status=task.status,
            result=task.result,
            error=task.error,
            verification=task.verification,
            instance_id=None if instance is None else instance.instance_id,
            attempts=task.attempt_count,
        )
