from __future__ import annotations

from pathlib import Path

import pytest

from tests.communication_fakes import (
    NOW,
    MutableClock,
    RecordingEventHandler,
    event_failure,
    event_success,
    full_authorizer,
)
from zyro.communication.contracts import (
    AcknowledgementOutcome,
    DeliveryStatus,
    EventAcknowledgement,
    EventPublishStatus,
    EventSubscription,
)
from zyro.communication.event_bus import DurableEventBus
from zyro.communication.persistence import (
    CommunicationPersistenceError,
    SQLiteCommunicationStore,
)
from zyro.core.events import Event, EventDelivery, RetryPolicy


class SimulatedCrash(BaseException):
    pass


def durable_event(
    event_id: str = "event-1",
    *,
    ordering_key: str | None = "lead-1",
    max_attempts: int = 3,
    backoff_seconds: float = 0,
    ack_required: bool = True,
) -> Event:
    return Event(
        event_id=event_id,
        request_id="request-1",
        task_id="task-1",
        workflow_id="workflow-1",
        correlation_id="correlation-1",
        event_type="LEAD_QUALIFIED",
        publisher="freelancing.qualification-pipeline",
        payload={"lead_id": event_id},
        delivery=EventDelivery(
            durable=True,
            ack_required=ack_required,
            ordering_key=ordering_key,
            retry_policy=RetryPolicy(max_attempts, backoff_seconds),
        ),
        timestamp=NOW,
        version="1.0",
    )


def bus(
    path: Path,
    *,
    clock: MutableClock | None = None,
) -> tuple[DurableEventBus, SQLiteCommunicationStore, MutableClock]:
    actual_clock = clock or MutableClock()
    store = SQLiteCommunicationStore(path)
    return (
        DurableEventBus(store, full_authorizer(), clock=actual_clock),
        store,
        actual_clock,
    )


def subscribe(
    event_bus: DurableEventBus,
    subscriber: str,
    handler: RecordingEventHandler,
) -> None:
    event_bus.subscribe(
        EventSubscription(subscriber, ("LEAD_QUALIFIED",)),
        handler,
    )


def test_event_envelope_round_trip_preserves_delivery_and_trace() -> None:
    original = durable_event()

    encoded = original.to_json()
    restored = Event.from_json(encoded)

    assert restored == original
    assert restored.to_json() == encoded
    assert restored.delivery.durable
    assert restored.delivery.ack_required
    assert restored.delivery.retry_policy.max_attempts == 3
    assert restored.request_id == "request-1"
    assert restored.workflow_id == "workflow-1"


def test_invalid_event_and_secret_payload_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        durable_event(event_id="")
    with pytest.raises(ValueError):
        Event.from_json("{}")
    with pytest.raises(ValueError):
        Event(
            "event-secret",
            "request-1",
            "task-1",
            "correlation-1",
            "LEAD_QUALIFIED",
            "freelancing.qualification-pipeline",
            {"secret": "must-not-persist"},
            NOW,
            "1.0",
        )
    event_bus, store, _ = bus(tmp_path / "invalid.sqlite")
    empty_key = event_bus.publish_result(durable_event("event-empty-key"), idempotency_key="")
    assert empty_key.status is EventPublishStatus.INVALID
    result = event_bus.publish_result(
        Event(
            "event-nondurable",
            "request-1",
            "task-1",
            "correlation-1",
            "LEAD_QUALIFIED",
            "freelancing.qualification-pipeline",
            {"lead_id": "lead-1"},
            NOW,
            "1.0",
        ),
        idempotency_key="nondurable",
    )
    assert result.status is EventPublishStatus.INVALID
    store.close()


def test_publication_is_durable_before_delivery_and_recovers_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.sqlite"
    first_bus, first_store, _ = bus(path)
    handler = RecordingEventHandler([event_success()])
    subscribe(first_bus, "subscriber-a", handler)
    publication = first_bus.publish_result(durable_event(), idempotency_key="logical-1")
    assert publication.status is EventPublishStatus.ACCEPTED
    assert first_store.deliveries("event-1")[0].status is DeliveryStatus.PENDING
    first_store.close()

    second_bus, second_store, _ = bus(path)
    recovered_handler = RecordingEventHandler([event_success()])
    subscribe(second_bus, "subscriber-a", recovered_handler)
    delivered = second_bus.deliver_pending()

    assert delivered[0].status is DeliveryStatus.ACKED
    assert recovered_handler.calls[0][0].correlation_id == "correlation-1"
    second_store.close()


def test_multiple_subscribers_ack_independently(tmp_path: Path) -> None:
    event_bus, store, _ = bus(tmp_path / "multiple.sqlite")
    handler_a = RecordingEventHandler([event_success()])
    handler_b = RecordingEventHandler([event_failure(retryable=False)])
    subscribe(event_bus, "subscriber-a", handler_a)
    subscribe(event_bus, "subscriber-b", handler_b)
    assert event_bus.publish(durable_event(), idempotency_key="multi")

    event_bus.deliver_pending(limit=10)
    records = {item.subscriber_id: item for item in store.deliveries("event-1")}

    assert records["subscriber-a"].status is DeliveryStatus.ACKED
    assert records["subscriber-b"].status is DeliveryStatus.DEAD_LETTER
    assert len(store.dead_letters()) == 1
    store.close()


def test_missing_ack_retries_to_bound_then_dead_letters(tmp_path: Path) -> None:
    event_bus, store, _ = bus(tmp_path / "dead.sqlite")
    handler = RecordingEventHandler([event_success(acknowledged=False)])
    subscribe(event_bus, "subscriber-a", handler)
    event_bus.publish(durable_event(max_attempts=2), idempotency_key="dead")

    results = event_bus.deliver_pending(limit=10)

    assert [item.status for item in results] == [
        DeliveryStatus.RETRY_WAIT,
        DeliveryStatus.DEAD_LETTER,
    ]
    assert len(handler.calls) == 2
    dead = store.dead_letters()[0]
    assert dead.event_id == "event-1"
    assert dead.subscriber_id == "subscriber-a"
    assert dead.attempts == 2
    assert dead.correlation_id == "correlation-1"
    assert len(store.attempts(dead.delivery_id)) == 2
    store.close()


def test_duplicate_event_identity_or_idempotency_key_is_not_republished(tmp_path: Path) -> None:
    event_bus, store, _ = bus(tmp_path / "dedup.sqlite")

    first = event_bus.publish_result(durable_event(), idempotency_key="same-logical")
    duplicate_id = event_bus.publish_result(durable_event(), idempotency_key="other-key")
    duplicate_key = event_bus.publish_result(
        durable_event("event-2"),
        idempotency_key="same-logical",
    )

    assert first.status is EventPublishStatus.ACCEPTED
    assert duplicate_id.status is EventPublishStatus.DUPLICATE
    assert duplicate_key.status is EventPublishStatus.DUPLICATE
    store.close()


def test_ordering_key_blocks_only_same_key(tmp_path: Path) -> None:
    clock = MutableClock()
    event_bus, store, _ = bus(tmp_path / "ordering.sqlite", clock=clock)
    handler = RecordingEventHandler([event_failure(), event_success(), event_success()])
    subscribe(event_bus, "subscriber-a", handler)
    event_bus.publish(
        durable_event("event-1", ordering_key="key-a", backoff_seconds=10),
        idempotency_key="one",
    )
    event_bus.publish(
        durable_event("event-2", ordering_key="key-a"),
        idempotency_key="two",
    )
    event_bus.publish(
        durable_event("event-3", ordering_key="key-b"),
        idempotency_key="three",
    )

    first_cycle = event_bus.deliver_pending(limit=5)

    assert [item.event_id for item in first_cycle] == ["event-1", "event-3"]
    assert store.deliveries("event-2")[0].status is DeliveryStatus.PENDING
    clock.advance(10)
    second_cycle = event_bus.deliver_pending(limit=5)
    assert [item.event_id for item in second_cycle] == ["event-1", "event-2"]
    store.close()


def test_crash_before_ack_recovers_as_at_least_once_delivery(tmp_path: Path) -> None:
    path = tmp_path / "crash.sqlite"
    first_bus, first_store, _ = bus(path)
    crashing = RecordingEventHandler([SimulatedCrash()])
    subscribe(first_bus, "subscriber-a", crashing)
    first_bus.publish(durable_event(max_attempts=3), idempotency_key="crash")

    with pytest.raises(SimulatedCrash):
        first_bus.deliver_pending(limit=1)
    assert first_store.deliveries()[0].status is DeliveryStatus.DELIVERING
    first_store.close()

    second_bus, second_store, _ = bus(path)
    assert second_bus.recovered_deliveries == 1
    recovered = RecordingEventHandler([event_success()])
    subscribe(second_bus, "subscriber-a", recovered)
    result = second_bus.deliver_pending()

    assert result[0].status is DeliveryStatus.ACKED
    assert result[0].attempt == 2
    assert len(second_store.attempts(result[0].delivery_id)) == 2
    second_store.close()


def test_retry_wait_and_exhausted_interruption_recover_after_restart(
    tmp_path: Path,
) -> None:
    retry_path = tmp_path / "retry-restart.sqlite"
    first_bus, first_store, clock = bus(retry_path)
    retrying = RecordingEventHandler([event_failure()])
    subscribe(first_bus, "subscriber-a", retrying)
    first_bus.publish(
        durable_event(backoff_seconds=10),
        idempotency_key="retry-restart",
    )
    first_bus.deliver_pending(limit=1)
    assert first_store.deliveries()[0].status is DeliveryStatus.RETRY_WAIT
    first_store.close()

    second_bus, second_store, _ = bus(retry_path, clock=clock)
    recovered = RecordingEventHandler([event_success()])
    subscribe(second_bus, "subscriber-a", recovered)
    assert second_bus.deliver_pending() == ()
    clock.advance(10)
    assert second_bus.deliver_pending()[0].status is DeliveryStatus.ACKED
    second_store.close()

    exhausted_path = tmp_path / "exhausted-crash.sqlite"
    crash_bus, crash_store, _ = bus(exhausted_path)
    subscribe(crash_bus, "subscriber-a", RecordingEventHandler([SimulatedCrash()]))
    crash_bus.publish(
        durable_event(max_attempts=1),
        idempotency_key="exhausted-crash",
    )
    with pytest.raises(SimulatedCrash):
        crash_bus.deliver_pending(limit=1)
    crash_store.close()

    terminal_bus, terminal_store, _ = bus(exhausted_path)
    assert terminal_bus.recovered_deliveries == 1
    assert terminal_store.deliveries()[0].status is DeliveryStatus.DEAD_LETTER
    assert terminal_store.dead_letters()[0].error_code == "delivery_interrupted"
    terminal_store.close()


def test_persisted_ack_and_dead_letter_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "terminal.sqlite"
    first_bus, first_store, _ = bus(path)
    acking = RecordingEventHandler([event_success()])
    failing = RecordingEventHandler([event_failure(retryable=False)])
    subscribe(first_bus, "subscriber-a", acking)
    subscribe(first_bus, "subscriber-b", failing)
    first_bus.publish(durable_event(), idempotency_key="terminal")
    first_bus.deliver_pending(limit=10)
    first_store.close()

    second_bus, second_store, _ = bus(path)
    subscribe(second_bus, "subscriber-a", RecordingEventHandler([event_success()]))
    subscribe(second_bus, "subscriber-b", RecordingEventHandler([event_success()]))

    assert second_bus.deliver_pending() == ()
    records = {item.subscriber_id: item.status for item in second_store.deliveries()}
    assert records == {
        "subscriber-a": DeliveryStatus.ACKED,
        "subscriber-b": DeliveryStatus.DEAD_LETTER,
    }
    assert len(second_store.dead_letters()) == 1
    second_store.close()


def test_invalid_stale_and_duplicate_acknowledgements_do_not_corrupt_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "acks.sqlite"
    event_bus, store, _ = bus(path)
    crashing = RecordingEventHandler([SimulatedCrash()])
    subscribe(event_bus, "subscriber-a", crashing)
    event_bus.publish(durable_event(), idempotency_key="acks")
    with pytest.raises(SimulatedCrash):
        event_bus.deliver_pending(limit=1)
    record = store.deliveries()[0]
    assert record.attempt_id is not None

    invalid = event_bus.acknowledge(
        EventAcknowledgement("event-1", "subscriber-a", "wrong-attempt", NOW)
    )
    accepted = event_bus.acknowledge(
        EventAcknowledgement("event-1", "subscriber-a", record.attempt_id, NOW)
    )
    duplicate = event_bus.acknowledge(
        EventAcknowledgement("event-1", "subscriber-a", record.attempt_id, NOW)
    )
    unknown = event_bus.acknowledge(EventAcknowledgement("missing", "subscriber-a", "attempt", NOW))

    assert invalid is AcknowledgementOutcome.INVALID
    assert accepted is AcknowledgementOutcome.ACKED
    assert duplicate is AcknowledgementOutcome.DUPLICATE
    assert unknown is AcknowledgementOutcome.INVALID
    assert store.deliveries()[0].status is DeliveryStatus.ACKED
    store.close()


def test_unknown_subscriber_remains_pending_until_handler_reregisters(tmp_path: Path) -> None:
    path = tmp_path / "unknown.sqlite"
    first_bus, first_store, _ = bus(path)
    subscribe(first_bus, "subscriber-a", RecordingEventHandler([event_success()]))
    first_bus.publish(durable_event(), idempotency_key="unknown")
    first_store.close()

    second_bus, second_store, _ = bus(path)

    assert second_bus.unknown_subscribers() == ("subscriber-a",)
    assert second_bus.deliver_pending() == ()
    assert second_store.deliveries()[0].status is DeliveryStatus.PENDING
    second_store.close()


def test_publish_and_consume_authorization_fail_closed(tmp_path: Path) -> None:
    from tests.communication_fakes import authorizer

    publish_store = SQLiteCommunicationStore(tmp_path / "publish-authorization.sqlite")
    denied_bus = DurableEventBus(publish_store, authorizer(()), clock=lambda: NOW)
    denied = denied_bus.publish_result(durable_event(), idempotency_key="denied")
    assert denied.status is EventPublishStatus.UNAUTHORIZED
    assert publish_store.deliveries() == ()
    publish_store.close()

    consume_store = SQLiteCommunicationStore(tmp_path / "consume-authorization.sqlite")
    publish_only = authorizer(
        (
            (
                "freelancing.qualification-pipeline",
                "communication.event.publish",
                "LEAD_QUALIFIED",
                "publish",
            ),
        )
    )
    event_bus = DurableEventBus(consume_store, publish_only, clock=lambda: NOW)
    handler = RecordingEventHandler([event_success()])
    subscribe(event_bus, "subscriber-a", handler)
    assert event_bus.publish(durable_event(), idempotency_key="consume-denied")

    result = event_bus.deliver_pending()

    assert result[0].status is DeliveryStatus.DEAD_LETTER
    assert handler.calls == []
    consume_store.close()


class FailingStore(SQLiteCommunicationStore):
    def persist_event(self, event: Event, idempotency_key: str, now: object) -> bool:
        raise CommunicationPersistenceError("fixture persistence failure")


def test_persistence_failure_is_structured(tmp_path: Path) -> None:
    store = FailingStore(tmp_path / "failure.sqlite")
    event_bus = DurableEventBus(store, full_authorizer(), clock=lambda: NOW)

    result = event_bus.publish_result(durable_event(), idempotency_key="failure")

    assert result.status is EventPublishStatus.PERSISTENCE_FAILURE
    assert result.error is not None
    store.close()
