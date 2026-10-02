"""Verification protocol and structural verifier kept separate from execution."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from zyro.agents.instance import AgentInstanceStatus
from zyro.core.errors import ErrorInfo
from zyro.core.task import Task
from zyro.core.verification import (
    VerificationEvidence,
    VerificationOutcome,
    VerificationResult,
    utc_now,
)
from zyro.runtime.agent_runtime import RuntimeExecution


class Verifier(Protocol):
    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        """Verify evidence independently of agent execution."""
        ...


class StructuralRuntimeVerifier:
    """Verify only runtime-result consistency, never semantic goal correctness."""

    verifier_id = "structural-runtime-verifier"

    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        now = utc_now()
        verification_id = str(uuid4())
        instance = runtime_result.instance
        execution_id = None if instance is None else instance.instance_id
        if runtime_result.execution.outcome_unknown:
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                evidence_type="unknown_execution_outcome",
                summary="Execution started, but available evidence cannot prove completion.",
                source=self.verifier_id,
                observed_at=now,
            )
            return VerificationResult(
                outcome=VerificationOutcome.UNKNOWN,
                summary=evidence.summary,
                scope="runtime_structure",
                verification_id=verification_id,
                task_id=task.task_id,
                execution_id=execution_id,
                verifier_id=self.verifier_id,
                verified_at=now,
                evidence=(evidence,),
            )

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
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                evidence_type="runtime_inconsistency",
                summary=error.message,
                source=self.verifier_id,
                observed_at=now,
            )
            return VerificationResult(
                outcome=VerificationOutcome.FAILED,
                summary=error.message,
                scope="runtime_structure",
                error=error,
                verification_id=verification_id,
                task_id=task.task_id,
                execution_id=execution_id,
                verifier_id=self.verifier_id,
                verified_at=now,
                evidence=(evidence,),
            )
        evidence = VerificationEvidence(
            evidence_id=str(uuid4()),
            evidence_type="runtime_consistency",
            summary="Task, agent instance, and structured execution result are consistent.",
            source=self.verifier_id,
            observed_at=now,
        )
        return VerificationResult(
            outcome=VerificationOutcome.VERIFIED,
            summary="Agent runtime completed and produced a consistent structured result.",
            scope="runtime_structure",
            verification_id=verification_id,
            task_id=task.task_id,
            execution_id=execution_id,
            verifier_id=self.verifier_id,
            verified_at=now,
            evidence=(evidence,),
        )


__all__ = [
    "StructuralRuntimeVerifier",
    "VerificationEvidence",
    "VerificationOutcome",
    "VerificationResult",
    "Verifier",
]
