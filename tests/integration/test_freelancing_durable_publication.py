from __future__ import annotations

from pathlib import Path

import pytest

from tests.communication_fakes import NOW, RecordingEventHandler, event_success, full_authorizer
from tests.integration.test_freelancing_pipeline import pipeline
from tests.unit.test_freelancing_evaluation import found_lead
from zyro.communication.contracts import DeliveryStatus, EventSubscription
from zyro.communication.event_bus import DurableEventBus
from zyro.communication.persistence import SQLiteCommunicationStore
from zyro.core.events import Event
from zyro.domains.freelancing.contracts import LeadState, PipelineOutcome


class FailOncePublisher:
    def __init__(self, delegate: DurableEventBus) -> None:
        self.delegate = delegate
        self.calls = 0

    def publish(self, event: Event, *, idempotency_key: str) -> bool:
        self.calls += 1
        if self.calls == 1:
            return False
        return self.delegate.publish(event, idempotency_key=idempotency_key)


def test_qualified_lead_publishes_durably_after_committed_state(tmp_path: Path) -> None:
    store = SQLiteCommunicationStore(tmp_path / "pipeline.sqlite")
    event_bus = DurableEventBus(store, full_authorizer(), clock=lambda: NOW)
    handler = RecordingEventHandler([event_success()])
    event_bus.subscribe(
        EventSubscription("subscriber-a", ("LEAD_QUALIFIED",)),
        handler,
    )
    subject, lead_store, _ = pipeline(publisher=event_bus)

    result = subject.process(found_lead(), owner="owner-1")

    assert result.outcome is PipelineOutcome.LEAD_QUALIFIED
    assert lead_store.get(result.lead.lead_id).state is LeadState.LEAD_QUALIFIED
    persisted = store.event(result.event_id or "")
    assert persisted is not None
    assert persisted.delivery.durable
    assert persisted.delivery.ack_required
    assert store.deliveries(result.event_id)[0].status is DeliveryStatus.PENDING
    event_bus.deliver_pending()
    assert handler.calls[0][0].payload["lead_id"] == result.lead.lead_id
    store.close()


def test_non_atomic_publication_boundary_has_idempotent_reconciliation(
    tmp_path: Path,
) -> None:
    store = SQLiteCommunicationStore(tmp_path / "reconcile.sqlite")
    event_bus = DurableEventBus(store, full_authorizer(), clock=lambda: NOW)
    flaky = FailOncePublisher(event_bus)
    subject, lead_store, _ = pipeline(publisher=flaky)

    failed = subject.process(found_lead(), owner="owner-1")

    assert failed.outcome is PipelineOutcome.PUBLICATION_FAILED
    assert lead_store.get(failed.lead.lead_id).state is LeadState.LEAD_QUALIFIED
    assert store.deliveries() == ()

    recovered = subject.reconcile_publication(failed.lead.lead_id)
    duplicate_reconciliation = subject.reconcile_publication(failed.lead.lead_id)

    assert recovered.outcome is PipelineOutcome.LEAD_QUALIFIED
    assert duplicate_reconciliation.outcome is PipelineOutcome.LEAD_QUALIFIED
    assert len(store.deliveries()) == 0  # no subscriber was configured
    assert store.event(recovered.event_id or "") is not None
    with pytest.raises(KeyError):
        store.event(duplicate_reconciliation.event_id or "")
    store.close()
