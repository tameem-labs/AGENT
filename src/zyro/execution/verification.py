"""Minimal verification contract kept separate from agent execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from zyro.agents.instance import AgentInstanceStatus
from zyro.core.errors import ErrorInfo
from zyro.core.task import Task
from zyro.runtime.agent_runtime import RuntimeExecution


class VerificationOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    outcome: VerificationOutcome
    summary: str
    scope: str | None = None
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if self.outcome is VerificationOutcome.FAILED and self.error is None:
            raise ValueError("failed verification requires a structured error")
        if self.outcome is not VerificationOutcome.FAILED and self.error is not None:
            raise ValueError("only failed verification can contain an error")


class Verifier(Protocol):
    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        """Verify evidence independently of agent execution."""
        ...


class StructuralRuntimeVerifier:
    """Verify only runtime-result consistency, never semantic goal correctness."""

    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        instance = runtime_result.instance
        if (
            not runtime_result.execution.succeeded
            or instance is None
            or instance.status is not AgentInstanceStatus.SUCCEEDED
            or instance.task_id != task.task_id
            or instance.request_id != task.request_id
            or instance.correlation_id != task.correlation_id
            or runtime_result.execution.value != task.result
            or instance.result != task.result
        ):
            error = ErrorInfo(
                code="runtime_verification_failed",
                message="Runtime result and task result are inconsistent.",
                error_type="VerificationFailure",
                retryable=True,
            )
            return VerificationResult(
                outcome=VerificationOutcome.FAILED,
                summary=error.message,
                scope="runtime_structure",
                error=error,
            )
        return VerificationResult(
            outcome=VerificationOutcome.VERIFIED,
            summary="Agent runtime completed and produced a consistent structured result.",
            scope="runtime_structure",
        )
