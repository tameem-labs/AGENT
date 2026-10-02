"""Explicit Resource exhaustion to Recovery input adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from zyro.core.errors import ErrorInfo
from zyro.core.events import Event, EventDelivery, EventPublisher, RetryPolicy
from zyro.recovery.contracts import (
    FailureClass,
    FailureIdentity,
    FailureRecord,
    SideEffectState,
)
from zyro.resources.contracts import ConsumptionOutcome, TokenConsumptionResult


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ResourceRecoveryBridge:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._publisher = publisher

    def failure(
        self,
        result: TokenConsumptionResult,
        identity: FailureIdentity,
    ) -> FailureRecord | None:
        if result.outcome is not ConsumptionOutcome.RESOURCE_LIMIT_REACHED:
            return None
        failure = FailureRecord(
            self._id_factory(),
            FailureClass.RESOURCE_EXHAUSTION,
            identity,
            ErrorInfo(
                "resource_limit_reached",
                "A configured hard token limit was reached.",
                "ResourceExhaustion",
            ),
            self._clock(),
            False,
            SideEffectState.NONE,
        )
        self._publish(result, identity, failure)
        return failure

    def _publish(
        self,
        result: TokenConsumptionResult,
        identity: FailureIdentity,
        failure: FailureRecord,
    ) -> None:
        if self._publisher is None:
            return
        event = Event(
            event_id=self._id_factory(),
            request_id=identity.request_id,
            task_id=identity.task_id,
            workflow_id=identity.workflow_id,
            correlation_id=identity.correlation_id,
            event_type="RESOURCE_LIMIT_REACHED",
            publisher="runtime.resources",
            payload={
                "failure_id": failure.failure_id,
                "task_used": result.task_used,
                "task_limit": result.task_limit,
                "workflow_used": result.workflow_used,
                "workflow_limit": result.workflow_limit,
                "attempted_units": result.units,
                "precision": result.precision.value,
            },
            delivery=EventDelivery(
                durable=True,
                ack_required=False,
                ordering_key=identity.task_id,
                retry_policy=RetryPolicy(max_attempts=1),
            ),
            timestamp=failure.occurred_at,
            version="1.0",
        )
        try:
            self._publisher.publish(
                event,
                idempotency_key=f"resource-limit:{failure.failure_id}",
            )
        except Exception:
            # Event transport cannot change the hard stop or Recovery input.
            return


__all__ = ["ResourceRecoveryBridge"]
