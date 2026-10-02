from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.communication_fakes import (
    NOW,
    RecordingMessageHandler,
    authorizer,
    full_authorizer,
    response,
)
from zyro.communication.contracts import (
    DirectMessage,
    MessageDeliveryPolicy,
    MessageDeliveryStatus,
    MessageHandlerResult,
    MessagePriority,
)
from zyro.communication.messages import DirectMessageService, MessageReceiverRegistry
from zyro.communication.persistence import SQLiteCommunicationStore


def message(**changes: object) -> DirectMessage:
    values: dict[str, object] = {
        "message_id": "message-1",
        "request_id": "request-1",
        "task_id": "task-1",
        "workflow_id": None,
        "correlation_id": "correlation-1",
        "sender": "sender-1",
        "receiver": "receiver-1",
        "message_type": "WORK_NOTICE",
        "priority": MessagePriority.NORMAL,
        "payload": {"work_id": "work-1"},
        "response_required": False,
        "timestamp": NOW,
        "version": "1.0",
    }
    values.update(changes)
    return DirectMessage(**values)  # type: ignore[arg-type]


def service(
    path: Path,
    handler: RecordingMessageHandler | None = None,
    *,
    communication_authorizer: object | None = None,
) -> tuple[DirectMessageService, SQLiteCommunicationStore, RecordingMessageHandler]:
    store = SQLiteCommunicationStore(path)
    receivers = MessageReceiverRegistry()
    actual_handler = handler or RecordingMessageHandler([response()])
    receivers.register("receiver-1", actual_handler)
    result = DirectMessageService(
        store,
        receivers,
        communication_authorizer or full_authorizer(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return result, store, actual_handler


def test_direct_message_envelope_round_trips_stably() -> None:
    original = message(workflow_id="workflow-1", response_required=True)

    encoded = original.to_json()
    restored = DirectMessage.from_json(encoded)

    assert restored == original
    assert restored.to_json() == encoded
    assert restored.workflow_id == "workflow-1"
    assert restored.correlation_id == "correlation-1"


def test_invalid_or_secret_message_envelope_fails_closed() -> None:
    with pytest.raises(ValueError):
        message(message_id="")
    with pytest.raises(ValueError):
        message(payload={"nested": {"access_token": "must-not-persist"}})
    with pytest.raises(ValueError):
        message(payload=["not-a-mapping"])
    with pytest.raises(ValueError):
        message(payload={"number": math.inf})
    with pytest.raises(ValueError):
        message(response_required="yes")
    with pytest.raises(ValueError):
        DirectMessage.from_json("{}")


def test_authorized_message_routes_acknowledges_and_preserves_trace(tmp_path: Path) -> None:
    handler = RecordingMessageHandler([response({"accepted": True})])
    transport, store, _ = service(tmp_path / "messages.sqlite", handler)

    result = transport.send(
        message(response_required=True),
        MessageDeliveryPolicy(max_attempts=2),
    )

    assert result.status is MessageDeliveryStatus.ACKED
    assert result.acknowledged
    assert result.response == {"accepted": True}
    assert result.request_id == "request-1"
    assert result.task_id == "task-1"
    assert result.correlation_id == "correlation-1"
    assert len(result.permission_decision_ids) == 2
    assert handler.calls == [message(response_required=True)]
    store.close()


def test_unknown_receiver_does_not_deliver(tmp_path: Path) -> None:
    store = SQLiteCommunicationStore(tmp_path / "unknown.sqlite")
    transport = DirectMessageService(
        store,
        MessageReceiverRegistry(),
        full_authorizer(),
        clock=lambda: NOW,
    )

    result = transport.send(message(), MessageDeliveryPolicy())

    assert result.status is MessageDeliveryStatus.UNKNOWN_RECEIVER
    assert result.attempts == 0
    store.close()


def test_send_or_receive_permission_denial_fails_closed(tmp_path: Path) -> None:
    denied = authorizer((("sender-1", "communication.message.send", "receiver-1", "send"),))
    handler = RecordingMessageHandler([response()])
    transport, store, _ = service(
        tmp_path / "unauthorized.sqlite",
        handler,
        communication_authorizer=denied,
    )

    result = transport.send(message(), MessageDeliveryPolicy())

    assert result.status is MessageDeliveryStatus.UNAUTHORIZED
    assert result.error is not None
    assert handler.calls == []
    store.close()


def test_timeout_and_handler_failure_retry_only_to_bound(tmp_path: Path) -> None:
    handler = RecordingMessageHandler([TimeoutError(), RuntimeError("not exposed")])
    transport, store, _ = service(tmp_path / "retry.sqlite", handler)

    result = transport.send(message(), MessageDeliveryPolicy(max_attempts=2))

    assert result.status is MessageDeliveryStatus.DEAD_LETTER
    assert result.attempts == 2
    assert len(handler.calls) == 2
    assert result.error is not None
    assert "not exposed" not in result.error.message
    store.close()


def test_retry_can_succeed_and_response_required_is_enforced(tmp_path: Path) -> None:
    handler = RecordingMessageHandler(
        [
            MessageHandlerResult(False),
            response({"result": "ok"}),
        ]
    )
    transport, store, _ = service(tmp_path / "eventual.sqlite", handler)

    result = transport.send(
        message(response_required=True),
        MessageDeliveryPolicy(max_attempts=2),
    )

    assert result.status is MessageDeliveryStatus.ACKED
    assert result.attempts == 2
    assert result.response == {"result": "ok"}
    store.close()


def test_message_id_deduplication_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "dedup.sqlite"
    first_handler = RecordingMessageHandler([response()])
    first, first_store, _ = service(path, first_handler)
    accepted = first.send(message(), MessageDeliveryPolicy())
    first_store.close()

    second_handler = RecordingMessageHandler([response()])
    second, second_store, _ = service(path, second_handler)
    duplicate = second.send(message(), MessageDeliveryPolicy())

    assert accepted.status is MessageDeliveryStatus.ACKED
    assert duplicate.status is MessageDeliveryStatus.DUPLICATE
    assert second_handler.calls == []
    second_store.close()


def test_message_identity_collision_fails_without_leaking_original_response(
    tmp_path: Path,
) -> None:
    path = tmp_path / "collision.sqlite"
    first, first_store, _ = service(
        path,
        RecordingMessageHandler([response({"private_result": "bounded"})]),
    )
    first.send(message(response_required=True), MessageDeliveryPolicy())
    first_store.close()

    second, second_store, handler = service(path)
    collision = second.send(
        message(request_id="different-request", response_required=True),
        MessageDeliveryPolicy(),
    )

    assert collision.status is MessageDeliveryStatus.FAILED
    assert collision.response is None
    assert collision.error is not None
    assert collision.error.code == "message_identity_conflict"
    assert handler.calls == []
    second_store.close()


class OutcomeFailingStore(SQLiteCommunicationStore):
    def update_message(self, *args: object, **kwargs: object) -> None:
        from zyro.communication.persistence import CommunicationPersistenceError

        raise CommunicationPersistenceError("fixture failure")


def test_outcome_persistence_failure_is_structured(tmp_path: Path) -> None:
    store = OutcomeFailingStore(tmp_path / "outcome-failure.sqlite")
    registry = MessageReceiverRegistry()
    registry.register("receiver-1", RecordingMessageHandler([response()]))
    transport = DirectMessageService(
        store,
        registry,
        full_authorizer(),
        clock=lambda: NOW,
    )

    result = transport.send(message(), MessageDeliveryPolicy())

    assert result.status is MessageDeliveryStatus.PERSISTENCE_FAILURE
    assert result.attempts == 1
    store.close()
