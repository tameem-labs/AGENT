"""Local durable at-least-once event bus backed by SQLite."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from zyro.communication.authorization import CommunicationAuthorizer
from zyro.communication.contracts import (
    AcknowledgementOutcome,
    DeliveryContext,
    DeliveryCycleResult,
    DeliveryStatus,
    EventAcknowledgement,
    EventHandler,
    EventHandlerResult,
    EventPublicationResult,
    EventPublishStatus,
    EventSubscription,
)
from zyro.communication.persistence import (
    CommunicationPersistenceError,
    SQLiteCommunicationStore,
)
from zyro.core.errors import ErrorInfo
from zyro.core.events import Event


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DurableEventBus:
    """Synchronous local dispatcher with durable at-least-once delivery state."""

    def __init__(
        self,
        store: SQLiteCommunicationStore,
        authorizer: CommunicationAuthorizer,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._authorizer = authorizer
        self._clock = clock
        self._handlers: dict[str, EventHandler] = {}
        self.recovered_deliveries = self._store.recover_inflight(self._clock())

    def subscribe(self, subscription: EventSubscription, handler: EventHandler) -> None:
        self._store.register_subscription(subscription, self._clock())
        if subscription.enabled:
            self._handlers[subscription.subscriber_id] = handler
        else:
            self._handlers.pop(subscription.subscriber_id, None)

    def publish(self, event: Event, *, idempotency_key: str) -> bool:
        result = self.publish_result(event, idempotency_key=idempotency_key)
        return result.status in {EventPublishStatus.ACCEPTED, EventPublishStatus.DUPLICATE}

    def publish_result(self, event: Event, *, idempotency_key: str) -> EventPublicationResult:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return EventPublicationResult(
                EventPublishStatus.INVALID,
                event.event_id,
                error=ErrorInfo(
                    "invalid_idempotency_key",
                    "Event idempotency key must be a non-empty string.",
                    "InvalidEvent",
                ),
            )
        if not event.delivery.durable:
            return EventPublicationResult(
                EventPublishStatus.INVALID,
                event.event_id,
                error=ErrorInfo(
                    "event_not_durable",
                    "DurableEventBus accepts only events with durable delivery enabled.",
                    "InvalidEvent",
                ),
            )
        try:
            authorization = self._authorizer.authorize_publish(event)
        except Exception as error:
            return EventPublicationResult(
                EventPublishStatus.UNAUTHORIZED,
                event.event_id,
                error=ErrorInfo(
                    "event_authorization_unavailable",
                    f"Event authorization failed closed after {type(error).__name__}.",
                    "AuthorizationFailure",
                ),
            )
        if not authorization.allowed:
            return EventPublicationResult(
                EventPublishStatus.UNAUTHORIZED,
                event.event_id,
                authorization.decision_ids,
                authorization.error,
            )
        try:
            persisted = self._store.persist_event(event, idempotency_key, self._clock())
        except CommunicationPersistenceError as error:
            return EventPublicationResult(
                EventPublishStatus.PERSISTENCE_FAILURE,
                event.event_id,
                authorization.decision_ids,
                ErrorInfo(
                    "event_persistence_failure",
                    "Event could not be durably persisted.",
                    type(error).__name__,
                ),
            )
        return EventPublicationResult(
            EventPublishStatus.ACCEPTED if persisted else EventPublishStatus.DUPLICATE,
            event.event_id,
            authorization.decision_ids,
        )

    def deliver_pending(self, *, limit: int = 100) -> tuple[DeliveryCycleResult, ...]:
        if limit < 1:
            raise ValueError("delivery limit must be positive")
        results: list[DeliveryCycleResult] = []
        for _ in range(limit):
            claimed = self._store.claim_next(
                self._clock(),
                tuple(sorted(self._handlers)),
            )
            if claimed is None:
                break
            event, delivery = claimed
            assert delivery.attempt_id is not None
            context = DeliveryContext(
                delivery.delivery_id,
                delivery.attempt_id,
                delivery.subscriber_id,
                delivery.attempts,
            )
            try:
                authorization = self._authorizer.authorize_consume(
                    delivery.subscriber_id,
                    event,
                )
            except Exception as exception:
                authorization_error = ErrorInfo(
                    "consume_authorization_unavailable",
                    f"Consume authorization failed closed after {type(exception).__name__}.",
                    "AuthorizationFailure",
                )
                status = self._fail(context, authorization_error, retryable=False)
                results.append(self._cycle(context, event, status, authorization_error))
                continue
            if not authorization.allowed:
                assert authorization.error is not None
                status = self._fail(context, authorization.error, retryable=False)
                results.append(self._cycle(context, event, status, authorization.error))
                continue

            handler = self._handlers[delivery.subscriber_id]
            try:
                handler_result = handler.handle(event, context)
                if not isinstance(handler_result, EventHandlerResult):
                    raise TypeError("subscriber returned an invalid handler result")
            except TimeoutError:
                error = ErrorInfo(
                    "event_handler_timeout",
                    "Event handler timed out.",
                    "TimeoutError",
                    retryable=True,
                )
                status = self._fail(context, error, retryable=True)
                results.append(self._cycle(context, event, status, error))
                continue
            except Exception as exception:
                error = ErrorInfo(
                    "event_handler_exception",
                    f"Event handler raised {type(exception).__name__}.",
                    type(exception).__name__,
                    retryable=True,
                )
                status = self._fail(context, error, retryable=True)
                results.append(self._cycle(context, event, status, error))
                continue

            acknowledged = handler_result.succeeded and (
                handler_result.acknowledged or not event.delivery.ack_required
            )
            if acknowledged:
                outcome = self.acknowledge(
                    EventAcknowledgement(
                        event.event_id,
                        delivery.subscriber_id,
                        delivery.attempt_id,
                        self._clock(),
                    )
                )
                if outcome is AcknowledgementOutcome.ACKED:
                    results.append(self._cycle(context, event, DeliveryStatus.ACKED, None))
                    continue
                error = ErrorInfo(
                    "event_acknowledgement_invalid",
                    "Generated acknowledgement did not match the active attempt.",
                    "AcknowledgementFailure",
                )
                status = self._fail(context, error, retryable=False)
                results.append(self._cycle(context, event, status, error))
                continue

            error = handler_result.error or ErrorInfo(
                "event_acknowledgement_missing",
                "Handler completed without required acknowledgement.",
                "AcknowledgementMissing",
                retryable=True,
            )
            status = self._fail(context, error, retryable=handler_result.retryable)
            results.append(self._cycle(context, event, status, error))
        return tuple(results)

    def acknowledge(self, acknowledgement: EventAcknowledgement) -> AcknowledgementOutcome:
        return self._store.acknowledge(acknowledgement)

    def unknown_subscribers(self) -> tuple[str, ...]:
        return self._store.pending_without_handlers(set(self._handlers))

    def _fail(
        self,
        context: DeliveryContext,
        error: ErrorInfo,
        *,
        retryable: bool,
    ) -> DeliveryStatus:
        return self._store.fail_delivery(
            context.delivery_id,
            context.attempt_id,
            self._clock(),
            error_code=error.code,
            error_type=error.error_type,
            retryable=retryable,
        )

    @staticmethod
    def _cycle(
        context: DeliveryContext,
        event: Event,
        status: DeliveryStatus,
        error: ErrorInfo | None,
    ) -> DeliveryCycleResult:
        return DeliveryCycleResult(
            context.delivery_id,
            event.event_id,
            context.subscriber_id,
            status,
            context.attempt,
            error,
        )


__all__ = ["DurableEventBus"]
