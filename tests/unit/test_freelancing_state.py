from datetime import UTC, datetime

from tests.unit.test_freelancing_evaluation import (
    found_lead,
    qualification_policy,
    scoring_policy,
    validation_policy,
)
from zyro.core.events import Event, EventDelivery, InProcessEventPublisher
from zyro.domains.freelancing.contracts import (
    LeadProvenance,
    LeadState,
    QualificationOutcome,
    ScoringOutcome,
    ValidationOutcome,
)
from zyro.domains.freelancing.evaluation import (
    LeadValidator,
    QualificationEvaluator,
    ScoringEvaluator,
)
from zyro.domains.freelancing.state import (
    InProcessLeadStore,
    IntakeOutcome,
    StateUpdateOutcome,
)

NOW = datetime(2026, 10, 2, tzinfo=UTC)


def provenance(stage: str, revision: int) -> LeadProvenance:
    return LeadProvenance(stage, f"task-{stage}", f"verify-{stage}", "policy-v1", revision)


def test_first_seen_duplicate_rule_preserves_one_authoritative_lead() -> None:
    store = InProcessLeadStore()
    first = store.ingest(found_lead())
    duplicate = store.ingest(found_lead(lead_id="lead-duplicate"))

    assert first.outcome is IntakeOutcome.CREATED
    assert duplicate.outcome is IntakeOutcome.DUPLICATE
    assert duplicate.lead.lead_id == "lead-1"
    assert duplicate.lead.revision == 0


def test_authoritative_state_transitions_preserve_revision_and_provenance() -> None:
    store = InProcessLeadStore(clock=lambda: NOW)
    lead = store.ingest(found_lead()).lead
    validation = LeadValidator().evaluate(lead, validation_policy())
    validated = store.commit_validation(
        lead.lead_id,
        0,
        validation,
        provenance("validation", 0),
    )
    qualification = QualificationEvaluator().evaluate(
        validated.lead,
        qualification_policy(),
    )
    qualified = store.commit_qualification(
        lead.lead_id,
        1,
        qualification,
        provenance("qualification", 1),
    )
    scoring = ScoringEvaluator().evaluate(qualified.lead, scoring_policy())
    scored = store.commit_scoring(
        lead.lead_id,
        2,
        scoring,
        provenance("scoring", 2),
    )

    assert validation.outcome is ValidationOutcome.VALID
    assert qualification.outcome is QualificationOutcome.QUALIFIED
    assert scoring.outcome is ScoringOutcome.SCORED
    assert scored.outcome is StateUpdateOutcome.UPDATED
    assert scored.lead.state is LeadState.LEAD_QUALIFIED
    assert scored.lead.revision == 3
    assert tuple(item.stage for item in scored.lead.provenance) == (
        "validation",
        "qualification",
        "scoring",
    )
    assert scored.lead.provenance[-1].verification_id == "verify-scoring"


def test_stale_update_is_rejected_without_overwriting_newer_state() -> None:
    store = InProcessLeadStore()
    lead = store.ingest(found_lead()).lead
    validation = LeadValidator().evaluate(lead, validation_policy())
    committed = store.commit_validation(
        lead.lead_id,
        0,
        validation,
        provenance("validation", 0),
    )

    stale = store.commit_validation(
        lead.lead_id,
        0,
        validation,
        provenance("other-validation", 0),
    )

    assert committed.outcome is StateUpdateOutcome.UPDATED
    assert stale.outcome is StateUpdateOutcome.STALE
    assert stale.lead == store.get(lead.lead_id)
    assert stale.lead.revision == 1
    assert len(stale.lead.provenance) == 1


def test_conflicting_state_update_cannot_skip_validation() -> None:
    store = InProcessLeadStore()
    lead = store.ingest(found_lead()).lead
    qualification = QualificationEvaluator().evaluate(lead, qualification_policy())

    update = store.commit_qualification(
        lead.lead_id,
        0,
        qualification,
        provenance("qualification", 0),
    )

    assert update.outcome is StateUpdateOutcome.INVALID_STATE
    assert store.get(lead.lead_id).state is LeadState.LEAD_FOUND
    assert store.get(lead.lead_id).revision == 0


def test_in_process_event_seam_is_non_durable_and_idempotent() -> None:
    publisher = InProcessEventPublisher()
    event = Event(
        "event-1",
        "request-1",
        "task-1",
        "correlation-1",
        "LEAD_QUALIFIED",
        "freelancing.pipeline",
        {"lead_id": "lead-1"},
        NOW,
        "1.0",
        delivery=EventDelivery(ordering_key="lead-1"),
    )

    assert publisher.publish(event, idempotency_key="lead-1:q1:s1")
    assert not publisher.publish(event, idempotency_key="lead-1:q1:s1")
    assert publisher.events() == (event,)
    assert not event.delivery.durable
