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

    def __post_init__(self) -> None:
        for field_name in ("goal", "requester", "agent_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidRequestError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        for field_name in ("request_id", "correlation_id"):
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
    ) -> None:
        self._runtime = runtime
        self._verifier = verifier
        self._id_factory = id_factory or (lambda: str(uuid4()))

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

        while True:
            last_runtime_result = self._runtime.execute(task, request.agent_id)
            if not last_runtime_result.execution.succeeded:
                if task.can_retry:
                    task.prepare_retry()
                    logger.info("retrying task after retryable execution failure")
                    continue
                return self._report(task, last_runtime_result, ExecutiveOutcome.FAILED)

            if self._verifier is None:
                task.mark_verification_unavailable("No verifier was configured for this task.")
                logger.warning("execution succeeded but remains unverified")
                return self._report(
                    task,
                    last_runtime_result,
                    ExecutiveOutcome.SUCCEEDED_UNVERIFIED,
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
                return self._report(task, last_runtime_result, ExecutiveOutcome.FAILED)

            if verification.outcome is VerificationOutcome.VERIFIED:
                task.mark_verified(verification.summary, verification.scope or "unspecified")
                task.complete()
                logger.info("task verified and completed")
                return self._report(
                    task,
                    last_runtime_result,
                    ExecutiveOutcome.VERIFIED_SUCCESS,
                )
            if verification.outcome is VerificationOutcome.UNAVAILABLE:
                task.mark_verification_unavailable(verification.summary)
                return self._report(
                    task,
                    last_runtime_result,
                    ExecutiveOutcome.SUCCEEDED_UNVERIFIED,
                )

            assert verification.error is not None
            scope = verification.scope or "unspecified"
            if verification.error.retryable and task.attempt_count < task.max_attempts:
                task.retry_after_verification_failure(verification.error, scope)
                logger.info("retrying task after retryable verification failure")
                continue
            task.mark_verification_failed(verification.error, scope)
            return self._report(task, last_runtime_result, ExecutiveOutcome.FAILED)

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
