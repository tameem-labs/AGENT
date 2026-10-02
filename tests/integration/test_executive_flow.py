from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, AgentHandler, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.errors import ErrorInfo, InvalidRequestError
from zyro.core.executive import ExecutiveOutcome, UserRequest, ZyroExecutive
from zyro.core.task import Task, TaskStatus, VerificationStatus
from zyro.execution.verification import (
    StructuralRuntimeVerifier,
    VerificationEvidence,
    VerificationOutcome,
    VerificationResult,
    Verifier,
)
from zyro.runtime.agent_runtime import AgentRuntime, RuntimeExecution

DEFAULT_VERIFIER = StructuralRuntimeVerifier()


def definition() -> AgentDefinition:
    return AgentDefinition(
        agent_id="bounded-agent",
        name="Bounded Agent",
        version="1.0",
        role="Execute deterministic test logic",
        domain="core",
        responsibilities=("return a bounded result",),
        capabilities=("deterministic_processing",),
        verification_requirements=("runtime structure",),
    )


class EchoHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        return AgentExecution.success({"goal": context.goal, "attempt": context.attempt})


@dataclass
class AlwaysRetryableFailureHandler:
    calls: int = 0

    def execute(self, context: ExecutionContext) -> AgentExecution:
        self.calls += 1
        return AgentExecution.failure(
            ErrorInfo("temporary", "Temporary failure.", "Temporary", retryable=True)
        )


@dataclass
class FailOnceHandler:
    calls: int = 0

    def execute(self, context: ExecutionContext) -> AgentExecution:
        self.calls += 1
        if self.calls == 1:
            return AgentExecution.failure(
                ErrorInfo("temporary", "Temporary failure.", "Temporary", retryable=True)
            )
        return AgentExecution.success({"attempt": context.attempt})


@dataclass
class RaisingVerifier:
    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        raise RuntimeError("sensitive verifier details must not escape")


@dataclass
class FailOnceVerifier:
    calls: int = 0

    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        self.calls += 1
        now = datetime(2026, 10, 2, tzinfo=UTC)
        assert runtime_result.instance is not None
        verification_id = f"verification-{self.calls}"
        execution_id = runtime_result.instance.instance_id
        verifier_id = "fail-once-test-verifier"
        if self.calls == 1:
            error = ErrorInfo(
                "evidence_failed",
                "Evidence failed once.",
                "VerificationFailure",
                retryable=True,
            )
            return VerificationResult(
                VerificationOutcome.FAILED,
                error.message,
                "test_verification",
                error,
                verification_id=verification_id,
                task_id=task.task_id,
                execution_id=execution_id,
                verifier_id=verifier_id,
                verified_at=now,
                evidence=(
                    VerificationEvidence(
                        "evidence-1",
                        "test_failure",
                        error.message,
                        verifier_id,
                        now,
                    ),
                ),
            )
        return VerificationResult(
            VerificationOutcome.VERIFIED,
            "Evidence passed.",
            "test_verification",
            verification_id=verification_id,
            task_id=task.task_id,
            execution_id=execution_id,
            verifier_id=verifier_id,
            verified_at=now,
            evidence=(
                VerificationEvidence(
                    "evidence-2",
                    "test_success",
                    "Evidence passed.",
                    verifier_id,
                    now,
                ),
            ),
        )


def executive(
    handler: AgentHandler,
    *,
    verifier: Verifier | None = DEFAULT_VERIFIER,
) -> ZyroExecutive:
    registry = AgentRegistry()
    registry.register(definition(), handler)
    runtime = AgentRuntime(registry, instance_id_factory=lambda: "instance-fixed")
    ids = iter(("request-generated", "correlation-generated", "task-fixed"))
    return ZyroExecutive(
        runtime,
        verifier=verifier,
        id_factory=lambda: next(ids),
    )


def test_executive_coordinates_request_to_verified_completion() -> None:
    result = executive(EchoHandler()).handle(
        UserRequest(
            goal="Process this request",
            requester="user-1",
            agent_id="bounded-agent",
            request_id="request-fixed",
            correlation_id="correlation-fixed",
        )
    )

    assert result.outcome is ExecutiveOutcome.VERIFIED_SUCCESS
    assert result.task_status is TaskStatus.DONE
    assert result.verification.status is VerificationStatus.VERIFIED
    assert result.verification.scope == "runtime_structure"
    assert result.result == {"goal": "Process this request", "attempt": 1}
    assert result.request_id == "request-fixed"
    assert result.correlation_id == "correlation-fixed"
    assert result.instance_id == "instance-fixed"


def test_success_without_verifier_is_explicitly_unverified() -> None:
    result = executive(EchoHandler(), verifier=None).handle(
        UserRequest("work", "user", "bounded-agent")
    )

    assert result.outcome is ExecutiveOutcome.SUCCEEDED_UNVERIFIED
    assert result.task_status is TaskStatus.VERIFYING
    assert result.verification.status is VerificationStatus.UNAVAILABLE


def test_executive_reports_missing_agent_as_structured_failure() -> None:
    result = executive(EchoHandler()).handle(UserRequest("work", "user", "missing-agent"))

    assert result.outcome is ExecutiveOutcome.FAILED
    assert result.task_status is TaskStatus.FAILED
    assert result.error is not None
    assert result.error.code == "agent_not_found"
    assert result.instance_id is None


def test_executive_retries_retryable_execution_with_hard_limit() -> None:
    handler = FailOnceHandler()
    result = executive(handler).handle(UserRequest("work", "user", "bounded-agent", max_attempts=2))

    assert result.outcome is ExecutiveOutcome.VERIFIED_SUCCESS
    assert result.attempts == 2
    assert handler.calls == 2


def test_executive_retries_verification_failure_separately() -> None:
    verifier = FailOnceVerifier()
    result = executive(EchoHandler(), verifier=verifier).handle(
        UserRequest("work", "user", "bounded-agent", max_attempts=2)
    )

    assert result.outcome is ExecutiveOutcome.VERIFIED_SUCCESS
    assert result.attempts == 2
    assert verifier.calls == 2


def test_retry_limit_prevents_infinite_execution_attempts() -> None:
    handler = AlwaysRetryableFailureHandler()
    result = executive(handler).handle(UserRequest("work", "user", "bounded-agent", max_attempts=2))

    assert result.outcome is ExecutiveOutcome.FAILED
    assert result.attempts == 2
    assert handler.calls == 2


def test_verifier_exception_becomes_structured_failure() -> None:
    result = executive(EchoHandler(), verifier=RaisingVerifier()).handle(
        UserRequest("work", "user", "bounded-agent")
    )

    assert result.outcome is ExecutiveOutcome.FAILED
    assert result.error is not None
    assert result.error.code == "verification_exception"
    assert result.error.error_type == "RuntimeError"
    assert "sensitive verifier details" not in result.error.message


def test_invalid_request_is_rejected_before_task_creation() -> None:
    with pytest.raises(InvalidRequestError, match="goal"):
        UserRequest(" ", "user", "bounded-agent")
