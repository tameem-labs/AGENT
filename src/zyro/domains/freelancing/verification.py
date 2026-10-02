"""Independent reproducibility verifier for Freelancing policy evaluations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from zyro.core.errors import ErrorInfo
from zyro.core.task import Task
from zyro.core.verification import (
    VerificationEvidence,
    VerificationOutcome,
    VerificationResult,
)
from zyro.domains.freelancing.agents import StageInputRegistry, StageKind
from zyro.domains.freelancing.contracts import (
    QualificationResult,
    ScoringResult,
    ValidationResult,
)
from zyro.domains.freelancing.evaluation import (
    LeadValidator,
    QualificationEvaluator,
    ScoringEvaluator,
)
from zyro.domains.freelancing.policies import (
    QualificationPolicy,
    ScoringPolicy,
    ValidationPolicy,
)
from zyro.domains.freelancing.state import LeadRepository
from zyro.runtime.agent_runtime import RuntimeExecution


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FreelancingPolicyVerifier:
    """Re-run the pure evaluator against the same authoritative lead revision and policy."""

    verifier_id = "freelancing-policy-verifier"

    def __init__(
        self,
        repository: LeadRepository,
        inputs: StageInputRegistry,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._inputs = inputs
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._validator = LeadValidator()
        self._qualifier = QualificationEvaluator()
        self._scorer = ScoringEvaluator()

    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        now = self._clock()
        verification_id = self._id_factory()
        evidence_id = self._id_factory()
        instance = runtime_result.instance
        execution_id = "missing-agent-instance" if instance is None else instance.instance_id
        stage_input = self._inputs.get(task.request_id)
        authoritative = self._repository.get(stage_input.lead.lead_id)
        if authoritative.revision != stage_input.lead.revision:
            return self._failure(
                task,
                execution_id,
                verification_id,
                evidence_id,
                now,
                "authoritative_revision_changed",
                "Authoritative lead revision changed before verification.",
            )

        expected: ValidationResult | QualificationResult | ScoringResult
        policy_version: str
        if stage_input.stage is StageKind.VALIDATION and isinstance(
            stage_input.policy, ValidationPolicy
        ):
            expected = self._validator.evaluate(authoritative, stage_input.policy)
            policy_version = stage_input.policy.version
        elif stage_input.stage is StageKind.QUALIFICATION and isinstance(
            stage_input.policy, QualificationPolicy
        ):
            expected = self._qualifier.evaluate(authoritative, stage_input.policy)
            policy_version = stage_input.policy.version
        elif stage_input.stage is StageKind.SCORING and isinstance(
            stage_input.policy, ScoringPolicy
        ):
            expected = self._scorer.evaluate(authoritative, stage_input.policy)
            policy_version = stage_input.policy.version
        else:
            return self._failure(
                task,
                execution_id,
                verification_id,
                evidence_id,
                now,
                "verification_input_invalid",
                "Stage input and policy type are inconsistent.",
            )

        if (
            not runtime_result.execution.succeeded
            or runtime_result.execution.value != expected
            or task.result != expected
        ):
            return self._failure(
                task,
                execution_id,
                verification_id,
                evidence_id,
                now,
                "policy_result_not_reproducible",
                "Policy result could not be reproduced from the authoritative revision.",
            )
        evidence = VerificationEvidence(
            evidence_id=evidence_id,
            evidence_type="policy_reproduction",
            summary=(
                f"{stage_input.stage.value} result reproduced at lead revision "
                f"{authoritative.revision} with policy {policy_version}."
            ),
            source=self.verifier_id,
            observed_at=now,
        )
        return VerificationResult(
            outcome=VerificationOutcome.VERIFIED,
            summary=evidence.summary,
            scope="freelancing_policy_reproduction",
            verification_id=verification_id,
            task_id=task.task_id,
            execution_id=execution_id,
            verifier_id=self.verifier_id,
            verified_at=now,
            evidence=(evidence,),
        )

    def _failure(
        self,
        task: Task,
        execution_id: str,
        verification_id: str,
        evidence_id: str,
        now: datetime,
        code: str,
        message: str,
    ) -> VerificationResult:
        error = ErrorInfo(code=code, message=message, error_type="VerificationFailure")
        evidence = VerificationEvidence(
            evidence_id=evidence_id,
            evidence_type=code,
            summary=message,
            source=self.verifier_id,
            observed_at=now,
        )
        return VerificationResult(
            outcome=VerificationOutcome.FAILED,
            summary=message,
            scope="freelancing_policy_reproduction",
            error=error,
            verification_id=verification_id,
            task_id=task.task_id,
            execution_id=execution_id,
            verifier_id=self.verifier_id,
            verified_at=now,
            evidence=(evidence,),
        )


__all__ = ["FreelancingPolicyVerifier"]
