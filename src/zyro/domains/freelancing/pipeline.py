"""Canonical verified Lead → Validation → Qualification → Scoring pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from zyro.agents.registry import AgentRegistry
from zyro.core.events import Event, EventDelivery, EventPublisher
from zyro.core.executive import ExecutiveOutcome, ExecutiveResult, UserRequest, ZyroExecutive
from zyro.domains.freelancing.agents import (
    QUALIFICATION_AGENT_ID,
    SCORING_AGENT_ID,
    VALIDATION_AGENT_ID,
    StageInput,
    StageInputRegistry,
    StageKind,
    register_freelancing_agents,
)
from zyro.domains.freelancing.contracts import (
    LeadProvenance,
    LeadRecord,
    PipelineOutcome,
    PipelineResult,
    QualificationOutcome,
    QualificationResult,
    ScoringOutcome,
    ScoringResult,
    ValidationOutcome,
    ValidationResult,
)
from zyro.domains.freelancing.policies import (
    QualificationPolicy,
    ScoringPolicy,
    ValidationPolicy,
)
from zyro.domains.freelancing.state import (
    IntakeOutcome,
    LeadRepository,
    StateUpdateOutcome,
)
from zyro.domains.freelancing.verification import FreelancingPolicyVerifier
from zyro.execution.verification import Verifier
from zyro.runtime.agent_runtime import AgentRuntime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FreelancingQualificationPipeline:
    """Domain orchestration that delegates every bounded step to Core Task/Agent/Verifier."""

    def __init__(
        self,
        repository: LeadRepository,
        publisher: EventPublisher,
        validation_policy: ValidationPolicy,
        qualification_policy: QualificationPolicy,
        scoring_policy: ScoringPolicy,
        *,
        verifier: Verifier | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._validation_policy = validation_policy
        self._qualification_policy = qualification_policy
        self._scoring_policy = scoring_policy
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock
        self.inputs = StageInputRegistry()
        registry = AgentRegistry()
        register_freelancing_agents(registry, self.inputs)
        runtime = AgentRuntime(registry, instance_id_factory=self._id_factory)
        actual_verifier = verifier or FreelancingPolicyVerifier(
            repository,
            self.inputs,
            clock=clock,
            id_factory=self._id_factory,
        )
        self._executive = ZyroExecutive(
            runtime,
            verifier=actual_verifier,
            id_factory=self._id_factory,
        )

    def process(self, lead: LeadRecord, *, owner: str) -> PipelineResult:
        try:
            intake = self._repository.ingest(lead)
        except ValueError as error:
            return PipelineResult(
                PipelineOutcome.INVALID,
                lead,
                reason=f"Lead intake rejected: {error}",
            )
        if intake.outcome is IntakeOutcome.DUPLICATE:
            return PipelineResult(
                PipelineOutcome.DUPLICATE,
                intake.lead,
                reason="Canonical first-seen lead already exists; no tasks were created.",
            )

        correlation_id = self._id_factory()
        task_ids: list[str] = []

        validation_run = self._run_stage(
            intake.lead,
            StageKind.VALIDATION,
            self._validation_policy,
            VALIDATION_AGENT_ID,
            owner,
            correlation_id,
        )
        task_ids.append(validation_run.task_id)
        if validation_run.outcome is not ExecutiveOutcome.VERIFIED_SUCCESS:
            return self._verification_failure(intake.lead, task_ids, validation_run)
        validation = validation_run.result
        if not isinstance(validation, ValidationResult):
            return self._verification_failure(intake.lead, task_ids, validation_run)
        validation_update = self._repository.commit_validation(
            intake.lead.lead_id,
            intake.lead.revision,
            validation,
            self._provenance(
                "validation", validation_run, validation.policy_version, lead.revision
            ),
        )
        if validation_update.outcome is not StateUpdateOutcome.UPDATED:
            return self._conflict(validation_update.lead, task_ids, validation_update.reason)
        current = validation_update.lead
        if validation.outcome is ValidationOutcome.INVALID:
            return PipelineResult(PipelineOutcome.INVALID, current, tuple(task_ids))
        if validation.outcome is ValidationOutcome.INSUFFICIENT_INFORMATION:
            return PipelineResult(
                PipelineOutcome.INSUFFICIENT_INFORMATION,
                current,
                tuple(task_ids),
            )

        qualification_run = self._run_stage(
            current,
            StageKind.QUALIFICATION,
            self._qualification_policy,
            QUALIFICATION_AGENT_ID,
            owner,
            correlation_id,
        )
        task_ids.append(qualification_run.task_id)
        if qualification_run.outcome is not ExecutiveOutcome.VERIFIED_SUCCESS:
            return self._verification_failure(current, task_ids, qualification_run)
        qualification = qualification_run.result
        if not isinstance(qualification, QualificationResult):
            return self._verification_failure(current, task_ids, qualification_run)
        qualification_update = self._repository.commit_qualification(
            current.lead_id,
            current.revision,
            qualification,
            self._provenance(
                "qualification",
                qualification_run,
                qualification.policy_version,
                current.revision,
            ),
        )
        if qualification_update.outcome is not StateUpdateOutcome.UPDATED:
            return self._conflict(
                qualification_update.lead,
                task_ids,
                qualification_update.reason,
            )
        current = qualification_update.lead
        if qualification.outcome is QualificationOutcome.NOT_QUALIFIED:
            return PipelineResult(PipelineOutcome.NOT_QUALIFIED, current, tuple(task_ids))
        if qualification.outcome is QualificationOutcome.INSUFFICIENT_INFORMATION:
            return PipelineResult(
                PipelineOutcome.INSUFFICIENT_INFORMATION,
                current,
                tuple(task_ids),
            )
        if qualification.outcome is QualificationOutcome.INVALID:
            return PipelineResult(PipelineOutcome.INVALID, current, tuple(task_ids))

        scoring_run = self._run_stage(
            current,
            StageKind.SCORING,
            self._scoring_policy,
            SCORING_AGENT_ID,
            owner,
            correlation_id,
        )
        task_ids.append(scoring_run.task_id)
        if scoring_run.outcome is not ExecutiveOutcome.VERIFIED_SUCCESS:
            return self._verification_failure(current, task_ids, scoring_run)
        scoring = scoring_run.result
        if not isinstance(scoring, ScoringResult):
            return self._verification_failure(current, task_ids, scoring_run)
        scoring_update = self._repository.commit_scoring(
            current.lead_id,
            current.revision,
            scoring,
            self._provenance(
                "scoring",
                scoring_run,
                scoring.policy_version,
                current.revision,
            ),
        )
        if scoring_update.outcome is not StateUpdateOutcome.UPDATED:
            return self._conflict(scoring_update.lead, task_ids, scoring_update.reason)
        current = scoring_update.lead
        if scoring.outcome is not ScoringOutcome.SCORED:
            return PipelineResult(PipelineOutcome.INVALID, current, tuple(task_ids))

        event = Event(
            event_id=self._id_factory(),
            request_id=scoring_run.request_id,
            task_id=scoring_run.task_id,
            correlation_id=correlation_id,
            event_type="LEAD_QUALIFIED",
            publisher="freelancing.qualification-pipeline",
            payload={
                "lead_id": current.lead_id,
                "lead_revision": current.revision,
                "qualification_policy_version": qualification.policy_version,
                "scoring_policy_version": scoring.policy_version,
                "score": scoring.total_score,
            },
            timestamp=self._clock(),
            version="1.0",
            delivery=EventDelivery(ordering_key=current.lead_id),
        )
        idempotency_key = (
            f"LEAD_QUALIFIED:{current.lead_id}:"
            f"{qualification.policy_version}:{scoring.policy_version}"
        )
        try:
            published = self._publisher.publish(event, idempotency_key=idempotency_key)
        except Exception:
            published = False
        if not published:
            return PipelineResult(
                PipelineOutcome.PUBLICATION_FAILED,
                current,
                tuple(task_ids),
                reason="Authoritative state committed, but bounded event publication failed.",
            )
        return PipelineResult(
            PipelineOutcome.LEAD_QUALIFIED,
            current,
            tuple(task_ids),
            event.event_id,
        )

    def _run_stage(
        self,
        lead: LeadRecord,
        stage: StageKind,
        policy: ValidationPolicy | QualificationPolicy | ScoringPolicy,
        agent_id: str,
        owner: str,
        correlation_id: str,
    ) -> ExecutiveResult:
        request_id = self._id_factory()
        self.inputs.bind(request_id, StageInput(stage, lead, policy))
        try:
            return self._executive.handle(
                UserRequest(
                    goal=f"{stage.value} lead {lead.lead_id} at revision {lead.revision}",
                    requester=owner,
                    agent_id=agent_id,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
            )
        finally:
            self.inputs.discard(request_id)

    @staticmethod
    def _provenance(
        stage: str,
        run: ExecutiveResult,
        policy_version: str,
        input_revision: int,
    ) -> LeadProvenance:
        verification_id = run.verification.verification_id
        if verification_id is None:
            raise ValueError("verified stage omitted verification identity")
        return LeadProvenance(
            stage,
            run.task_id,
            verification_id,
            policy_version,
            input_revision,
        )

    @staticmethod
    def _verification_failure(
        lead: LeadRecord,
        task_ids: list[str],
        run: ExecutiveResult,
    ) -> PipelineResult:
        reason = None if run.error is None else run.error.message
        return PipelineResult(
            PipelineOutcome.VERIFICATION_FAILED,
            lead,
            tuple(task_ids),
            reason=reason or "Stage did not reach verified completion.",
        )

    @staticmethod
    def _conflict(
        lead: LeadRecord,
        task_ids: list[str],
        reason: str,
    ) -> PipelineResult:
        return PipelineResult(
            PipelineOutcome.CONFLICT,
            lead,
            tuple(task_ids),
            reason=reason,
        )


__all__ = ["FreelancingQualificationPipeline"]
