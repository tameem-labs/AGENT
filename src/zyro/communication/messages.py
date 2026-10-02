"""Authorized point-to-point Direct Message service with durable deduplication."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from zyro.communication.authorization import CommunicationAuthorizer
from zyro.communication.contracts import (
    DirectMessage,
    MessageDeliveryPolicy,
    MessageDeliveryResult,
    MessageDeliveryStatus,
    MessageHandler,
    MessageHandlerResult,
)
from zyro.communication.persistence import (
    CommunicationPersistenceError,
    SQLiteCommunicationStore,
)
from zyro.core.errors import ErrorInfo


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MessageReceiverRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, MessageHandler] = {}

    def register(self, receiver_id: str, handler: MessageHandler) -> None:
        if not receiver_id.strip() or receiver_id in self._handlers:
            raise ValueError("receiver identity must be non-empty and unique")
        self._handlers[receiver_id] = handler

    def resolve(self, receiver_id: str) -> MessageHandler | None:
        return self._handlers.get(receiver_id)


class DirectMessageService:
    """Synchronous bounded transport; message metadata is durable, handlers remain injected."""

    def __init__(
        self,
        store: SQLiteCommunicationStore,
        receivers: MessageReceiverRegistry,
        authorizer: CommunicationAuthorizer,
        *,
        clock: Callable[[], datetime] = _utc_now,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._store = store
        self._receivers = receivers
        self._authorizer = authorizer
        self._clock = clock
        self._timer = timer

    def send(
        self,
        message: DirectMessage,
        policy: MessageDeliveryPolicy,
    ) -> MessageDeliveryResult:
        try:
            created = self._store.create_message(message, self._clock())
        except CommunicationPersistenceError:
            return self._result(
                message,
                MessageDeliveryStatus.PERSISTENCE_FAILURE,
                0,
                error=ErrorInfo(
                    "message_persistence_failure",
                    "Direct message could not be durably recorded.",
                    "CommunicationPersistenceError",
                ),
            )
        if not created:
            record = self._store.message_record(message.message_id)
            if record is None or record["envelope_json"] != message.to_json():
                return self._result(
                    message,
                    MessageDeliveryStatus.FAILED,
                    0 if record is None else int(record["attempts"]),
                    error=ErrorInfo(
                        "message_identity_conflict",
                        "Message identity is already bound to a different envelope.",
                        "IdentityConflict",
                    ),
                )
            attempts = int(record["attempts"])
            response = None
            if record["response_json"] is not None:
                response = json.loads(record["response_json"])
            return self._result(
                message,
                MessageDeliveryStatus.DUPLICATE,
                attempts,
                response=response,
            )

        try:
            authorization = self._authorizer.authorize_message(message)
        except Exception as exception:
            error = ErrorInfo(
                "message_authorization_unavailable",
                f"Message authorization failed closed after {type(exception).__name__}.",
                "AuthorizationFailure",
            )
            return self._finish(
                message,
                MessageDeliveryStatus.UNAUTHORIZED,
                0,
                error=error,
            )
        if not authorization.allowed:
            assert authorization.error is not None
            return self._finish(
                message,
                MessageDeliveryStatus.UNAUTHORIZED,
                0,
                error=authorization.error,
                permission_ids=authorization.decision_ids,
            )

        receiver = self._receivers.resolve(message.receiver)
        if receiver is None:
            error = ErrorInfo(
                "unknown_message_receiver",
                "Direct message receiver is not registered.",
                "UnknownReceiver",
            )
            return self._finish(
                message,
                MessageDeliveryStatus.UNKNOWN_RECEIVER,
                0,
                error=error,
                permission_ids=authorization.decision_ids,
            )

        last_error: ErrorInfo | None = None
        for attempt in range(1, policy.max_attempts + 1):
            started = self._timer()
            try:
                handler_result = receiver.handle(message)
                elapsed = self._timer() - started
                if not isinstance(handler_result, MessageHandlerResult):
                    raise TypeError("receiver returned an invalid handler result")
                if elapsed > policy.timeout_seconds:
                    raise TimeoutError
            except TimeoutError:
                last_error = ErrorInfo(
                    "message_delivery_timeout",
                    "Direct message delivery timed out.",
                    "TimeoutError",
                    retryable=True,
                )
                continue
            except Exception as exception:
                last_error = ErrorInfo(
                    "message_handler_exception",
                    f"Message handler raised {type(exception).__name__}.",
                    type(exception).__name__,
                    retryable=True,
                )
                continue

            if not handler_result.acknowledged:
                last_error = ErrorInfo(
                    "message_acknowledgement_missing",
                    "Receiver did not acknowledge the direct message.",
                    "AcknowledgementMissing",
                    retryable=True,
                )
                continue
            if message.response_required and handler_result.response is None:
                last_error = ErrorInfo(
                    "message_response_missing",
                    "A response was required but the receiver returned none.",
                    "ResponseMissing",
                    retryable=True,
                )
                continue
            return self._finish(
                message,
                MessageDeliveryStatus.ACKED,
                attempt,
                response=handler_result.response,
                permission_ids=authorization.decision_ids,
            )

        assert last_error is not None
        return self._finish(
            message,
            MessageDeliveryStatus.DEAD_LETTER,
            policy.max_attempts,
            error=last_error,
            permission_ids=authorization.decision_ids,
        )

    def _finish(
        self,
        message: DirectMessage,
        status: MessageDeliveryStatus,
        attempts: int,
        *,
        response: Mapping[str, Any] | None = None,
        error: ErrorInfo | None = None,
        permission_ids: tuple[str, ...] = (),
    ) -> MessageDeliveryResult:
        try:
            self._store.update_message(
                message.message_id,
                status,
                attempts,
                self._clock(),
                response=response,
                error_code=None if error is None else error.code,
                error_type=None if error is None else error.error_type,
            )
        except CommunicationPersistenceError:
            return self._result(
                message,
                MessageDeliveryStatus.PERSISTENCE_FAILURE,
                attempts,
                error=ErrorInfo(
                    "message_persistence_failure",
                    "Direct message delivery outcome could not be durably recorded.",
                    "CommunicationPersistenceError",
                ),
                permission_ids=permission_ids,
            )
        return self._result(
            message,
            status,
            attempts,
            response=response,
            error=error,
            permission_ids=permission_ids,
        )

    @staticmethod
    def _result(
        message: DirectMessage,
        status: MessageDeliveryStatus,
        attempts: int,
        *,
        response: Mapping[str, Any] | None = None,
        error: ErrorInfo | None = None,
        permission_ids: tuple[str, ...] = (),
    ) -> MessageDeliveryResult:
        return MessageDeliveryResult(
            status,
            message.message_id,
            message.request_id,
            message.task_id,
            message.correlation_id,
            message.receiver,
            attempts,
            response,
            error,
            permission_ids,
        )


__all__ = ["DirectMessageService", "MessageReceiverRegistry"]
