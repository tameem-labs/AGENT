from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

from tests.unit.test_freelancing_evaluation import (
    evidence,
    found_lead,
    qualification_policy,
    scoring_policy,
    validation_policy,
)
from zyro.agents.handler import AgentExecution
from zyro.agents.instance import AgentInstance
from zyro.core.errors import ErrorInfo
from zyro.core.events import Event, EventPublisher, InProcessEventPublisher
from zyro.core.task import Task
from zyro.core.verification import (
    VerificationEvidence,
    VerificationOutcome,
    VerificationResult,
)
from zyro.domains.freelancing.agents import StageInput, StageInputRegistry, StageKind
from zyro.domains.freelancing.contracts import (
    LeadProvenance,
    LeadState,
    PipelineOutcome,
    QualificationOutcome,
    ValidationOutcome,
    ValidationResult,
)
from zyro.domains.freelancing.evaluation import LeadValidator
from zyro.domains.freelancing.pipeline import FreelancingQualificationPipeline
from zyro.domains.freelancing.policies import CriterionOperator, Predicate
from zyro.domains.freelancing.state import InProcessLeadStore, StateUpdateResult
from zyro.domains.freelancing.verification import FreelancingPolicyVerifier
from zyro.execution.verification import Verifier
from zyro.runtime.agent_runtime import RuntimeExecution

NOW = datetime(2026, 10, 2, tzinfo=UTC)


def ids() -> object:
    values = count(1)
    return lambda: f"id-{next(values)}"


def pipeline(
    store: InProcessLeadStore | None = None,
    publisher: EventPublisher | None = None,
    *,
    verifier: Verifier | None = None,
    qualification: object | None = None,
) -> tuple[FreelancingQualificationPipeline, InProcessLeadStore, EventPublisher]:
    actual_store = store or InProcessLeadStore(clock=lambda: NOW)
    actual_publisher = publisher or InProcessEventPublisher()
    actual_pipeline = FreelancingQualificationPipeline(
        actual_store,
        actual_publisher,
        validation_policy(),
        qualification or qualification_policy(),  # type: ignore[arg-type]
        scoring_policy(),
        verifier=verifier,
        id_factory=ids(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return actual_pipeline, actual_store, actual_publisher


class RecordingPublisher:
    def __init__(self, store: InProcessLeadStore) -> None:
        self.store = store
        self.events: list[Event] = []
        self.committed_states: list[LeadState] = []

    def publish(self, event: Event, *, idempotency_key: str) -> bool:
        self.committed_states.append(self.store.get(event.payload["lead_id"]).state)
        self.events.append(event)
        return True


class FixedVerifier:
    def __init__(self, outcome: VerificationOutcome) -> None:
        self.outcome = outcome

    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        assert runtime_result.instance is not None
        evidence_item = VerificationEvidence(
            "fixed-evidence",
            "fixed_verification",
            "Fixed verifier did not establish reproducibility.",
            "fixed-verifier",
            NOW,
        )
        error = (
            ErrorInfo(
                "fixed_verification_failed",
                evidence_item.summary,
                "VerificationFailure",
            )
            if self.outcome is VerificationOutcome.FAILED
            else None
        )
        return VerificationResult(
            self.outcome,
            evidence_item.summary,
            "test",
            error,
            "fixed-verification",
            task.task_id,
            runtime_result.instance.instance_id,
            "fixed-verifier",
            NOW,
            (evidence_item,),
        )


class FailNthVerifier:
    def __init__(self, fail_on: int) -> None:
        self.fail_on = fail_on
        self.calls = 0

    def verify(self, task: Task, runtime_result: RuntimeExecution) -> VerificationResult:
        self.calls += 1
        assert runtime_result.instance is not None
        failed = self.calls == self.fail_on
        message = "Injected stage verification failure." if failed else "Injected verification."
        evidence_item = VerificationEvidence(
            f"evidence-{self.calls}",
            "injected_verification",
            message,
            "nth-verifier",
            NOW,
        )
        error = ErrorInfo("injected_failure", message, "VerificationFailure") if failed else None
        return VerificationResult(
            VerificationOutcome.FAILED if failed else VerificationOutcome.VERIFIED,
            message,
            "test",
            error,
            f"verification-{self.calls}",
            task.task_id,
            runtime_result.instance.instance_id,
            "nth-verifier",
            NOW,
            (evidence_item,),
        )


class RacingStore(InProcessLeadStore):
    def __init__(self) -> None:
        super().__init__(clock=lambda: NOW)
        self.raced = False

    def commit_validation(
        self,
        lead_id: str,
        expected_revision: int,
        result: ValidationResult,
        provenance: LeadProvenance,
    ) -> StateUpdateResult:
        if not self.raced:
            self.raced = True
            super().commit_validation(
                lead_id,
                expected_revision,
                result,
                LeadProvenance(
                    "concurrent-validation",
                    "concurrent-task",
                    "concurrent-verification",
                    result.policy_version,
                    expected_revision,
                ),
            )
        return super().commit_validation(
            lead_id,
            expected_revision,
            result,
            provenance,
        )


def test_verified_pipeline_commits_before_lead_qualified_event() -> None:
    store = InProcessLeadStore(clock=lambda: NOW)
    publisher = RecordingPublisher(store)
    service, _, _ = pipeline(store, publisher)

    result = service.process(found_lead(), owner="owner-1")

    assert result.outcome is PipelineOutcome.LEAD_QUALIFIED
    assert result.lead.state is LeadState.LEAD_QUALIFIED
    assert result.lead.revision == 3
    assert len(result.task_ids) == 3
    assert result.event_id is not None
    assert publisher.committed_states == [LeadState.LEAD_QUALIFIED]
    event = publisher.events[0]
    assert event.event_type == "LEAD_QUALIFIED"
    assert event.payload["score"] == 100
    assert event.task_id == result.task_ids[-1]
    assert tuple(item.stage for item in result.lead.provenance) == (
        "validation",
        "qualification",
        "scoring",
    )


def test_duplicate_processing_creates_no_tasks_writes_scores_or_events() -> None:
    service, store, publisher = pipeline()
    first = service.process(found_lead(), owner="owner-1")
    second = service.process(found_lead(lead_id="second-id"), owner="owner-1")

    assert first.outcome is PipelineOutcome.LEAD_QUALIFIED
    assert second.outcome is PipelineOutcome.DUPLICATE
    assert second.task_ids == ()
    assert second.lead == store.get("lead-1")
    assert second.lead.revision == 3
    assert second.lead.scoring == first.lead.scoring
    assert isinstance(publisher, InProcessEventPublisher)
    assert len(publisher.events("LEAD_QUALIFIED")) == 1


def test_invalid_intake_state_returns_structured_invalid_without_tasks() -> None:
    service, _, publisher = pipeline()

    result = service.process(
        found_lead(state=LeadState.VALIDATED),
        owner="owner-1",
    )

    assert result.outcome is PipelineOutcome.INVALID
    assert result.task_ids == ()
    assert result.reason is not None
    assert isinstance(publisher, InProcessEventPublisher)
    assert publisher.events() == ()


def test_invalid_lead_stops_after_verified_validation() -> None:
    lead = found_lead(
        research_evidence=(
            evidence("budget-a", "budget", 1000),
            evidence("budget-b", "budget", 900),
        )
    )
    service, _, publisher = pipeline()

    result = service.process(lead, owner="owner-1")

    assert result.outcome is PipelineOutcome.INVALID
    assert result.lead.validation is not None
    assert result.lead.validation.outcome is ValidationOutcome.INVALID
    assert result.lead.state is LeadState.REJECTED
    assert len(result.task_ids) == 1
    assert isinstance(publisher, InProcessEventPublisher)
    assert publisher.events() == ()


def test_insufficient_information_stops_before_qualification() -> None:
    lead = found_lead(fields={"title": "Missing budget"}, research_evidence=())
    service, _, publisher = pipeline()

    result = service.process(lead, owner="owner-1")

    assert result.outcome is PipelineOutcome.INSUFFICIENT_INFORMATION
    assert result.lead.state is LeadState.PENDING
    assert len(result.task_ids) == 1
    assert isinstance(publisher, InProcessEventPublisher)
    assert publisher.events() == ()


def test_not_qualified_stops_before_scoring_and_event() -> None:
    lead = found_lead(
        fields={"title": "Small", "budget": 100, "category": "python"},
        research_evidence=(
            evidence("title", "title", "Small"),
            evidence("budget", "budget", 100),
            evidence("category", "category", "python"),
        ),
    )
    service, _, publisher = pipeline()

    result = service.process(lead, owner="owner-1")

    assert result.outcome is PipelineOutcome.NOT_QUALIFIED
    assert result.lead.qualification is not None
    assert result.lead.qualification.outcome is QualificationOutcome.NOT_QUALIFIED
    assert result.lead.scoring is None
    assert result.lead.state is LeadState.REJECTED
    assert len(result.task_ids) == 2
    assert isinstance(publisher, InProcessEventPublisher)
    assert publisher.events() == ()


def test_qualification_missing_evidence_stops_before_scoring() -> None:
    policy = qualification_policy(
        Predicate(
            "timeline",
            "timeline_days",
            CriterionOperator.MAXIMUM,
            30,
            evidence_required=True,
        )
    )
    service, _, publisher = pipeline(qualification=policy)

    result = service.process(found_lead(), owner="owner-1")

    assert result.outcome is PipelineOutcome.INSUFFICIENT_INFORMATION
    assert len(result.task_ids) == 2
    assert result.lead.state is LeadState.PENDING
    assert isinstance(publisher, InProcessEventPublisher)
    assert publisher.events() == ()


def test_failed_or_unverified_stage_blocks_state_and_event() -> None:
    for outcome in (VerificationOutcome.FAILED, VerificationOutcome.UNKNOWN):
        service, store, publisher = pipeline(verifier=FixedVerifier(outcome))

        result = service.process(
            found_lead(lead_id=f"lead-{outcome.value}", canonical_key=f"key-{outcome.value}"),
            owner="owner-1",
        )

        assert result.outcome is PipelineOutcome.VERIFICATION_FAILED
        assert result.lead.state is LeadState.LEAD_FOUND
        assert store.get(result.lead.lead_id).revision == 0
        assert isinstance(publisher, InProcessEventPublisher)
        assert publisher.events() == ()


def test_verified_qualification_is_required_before_scoring_task() -> None:
    service, _, publisher = pipeline(verifier=FailNthVerifier(2))

    result = service.process(found_lead(), owner="owner-1")

    assert result.outcome is PipelineOutcome.VERIFICATION_FAILED
    assert len(result.task_ids) == 2
    assert result.lead.state is LeadState.VALIDATED
    assert result.lead.qualification is None
    assert isinstance(publisher, InProcessEventPublisher)
    assert publisher.events() == ()


def test_verified_scoring_is_required_before_final_state_and_event() -> None:
    service, _, publisher = pipeline(verifier=FailNthVerifier(3))

    result = service.process(found_lead(), owner="owner-1")

    assert result.outcome is PipelineOutcome.VERIFICATION_FAILED
    assert len(result.task_ids) == 3
    assert result.lead.state is LeadState.VALIDATED
    assert result.lead.qualification is not None
    assert result.lead.scoring is None
    assert isinstance(publisher, InProcessEventPublisher)
    assert publisher.events() == ()


def test_stale_commit_returns_conflict_and_does_not_publish() -> None:
    store = RacingStore()
    service, _, publisher = pipeline(store)

    result = service.process(found_lead(), owner="owner-1")

    assert result.outcome is PipelineOutcome.CONFLICT
    assert result.lead.revision == 1
    assert result.lead.state is LeadState.VALIDATED
    assert isinstance(publisher, InProcessEventPublisher)
    assert publisher.events() == ()


def test_policy_verifier_accepts_reproducible_authoritative_result() -> None:
    store = InProcessLeadStore(clock=lambda: NOW)
    lead = store.ingest(found_lead()).lead
    policy = validation_policy()
    inputs = StageInputRegistry()
    inputs.bind("request-1", StageInput(StageKind.VALIDATION, lead, policy))
    result = LeadValidator().evaluate(lead, policy)
    task = Task("task-1", "request-1", "correlation-1", "validate", "owner-1")
    task.start()
    task.record_execution_success(result)
    instance = AgentInstance(
        "instance-1",
        "validation-agent",
        task.task_id,
        task.request_id,
        task.correlation_id,
    )
    instance.start()
    instance.succeed(result)
    runtime = RuntimeExecution(instance, AgentExecution.success(result))
    verifier = FreelancingPolicyVerifier(
        store,
        inputs,
        clock=lambda: NOW,
        id_factory=ids(),  # type: ignore[arg-type]
    )

    verification = verifier.verify(task, runtime)

    assert verification.outcome is VerificationOutcome.VERIFIED
    assert verification.evidence[0].evidence_type == "policy_reproduction"


def test_policy_verifier_rejects_changed_authoritative_revision() -> None:
    store = InProcessLeadStore(clock=lambda: NOW)
    lead = store.ingest(found_lead()).lead
    policy = validation_policy()
    inputs = StageInputRegistry()
    inputs.bind("request-1", StageInput(StageKind.VALIDATION, lead, policy))
    result = LeadValidator().evaluate(lead, policy)
    task = Task("task-1", "request-1", "correlation-1", "validate", "owner-1")
    task.start()
    task.record_execution_success(result)
    instance = AgentInstance(
        "instance-1",
        "validation-agent",
        task.task_id,
        task.request_id,
        task.correlation_id,
    )
    instance.start()
    instance.succeed(result)
    runtime = RuntimeExecution(instance, AgentExecution.success(result))
    store.commit_validation(
        lead.lead_id,
        0,
        result,
        LeadProvenance("validation", "other-task", "other-verification", policy.version, 0),
    )
    verifier = FreelancingPolicyVerifier(
        store,
        inputs,
        clock=lambda: NOW,
        id_factory=ids(),  # type: ignore[arg-type]
    )

    verification = verifier.verify(task, runtime)

    assert verification.outcome is VerificationOutcome.FAILED
    assert verification.error is not None
    assert verification.error.code == "authoritative_revision_changed"
