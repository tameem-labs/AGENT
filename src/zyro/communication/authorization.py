"""Communication authorization composed from the existing Phase 4 permission evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from zyro.communication.contracts import DirectMessage
from zyro.core.errors import ErrorInfo
from zyro.core.events import Event
from zyro.security.permission import (
    PermissionEvaluationRequest,
    PermissionEvaluator,
    PermissionScope,
)


@dataclass(frozen=True, slots=True)
class CommunicationAuthorizationDecision:
    allowed: bool
    decision_ids: tuple[str, ...]
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if self.allowed == (self.error is not None):
            raise ValueError("authorization decision must contain an error only when denied")


class CommunicationAuthorizer(Protocol):
    def authorize_message(self, message: DirectMessage) -> CommunicationAuthorizationDecision: ...

    def authorize_publish(self, event: Event) -> CommunicationAuthorizationDecision: ...

    def authorize_consume(
        self,
        subscriber_id: str,
        event: Event,
    ) -> CommunicationAuthorizationDecision: ...


class PermissionCommunicationAuthorizer:
    """No new authority model: all communication authority is standing Phase 4 permission."""

    def __init__(self, evaluator: PermissionEvaluator) -> None:
        self._evaluator = evaluator

    def authorize_message(self, message: DirectMessage) -> CommunicationAuthorizationDecision:
        send = self._evaluator.evaluate(
            PermissionEvaluationRequest(
                principal_id=message.sender,
                capability="communication.message.send",
                scope=PermissionScope(target=message.receiver, action="send"),
                context={"message_type": message.message_type},
            )
        )
        if not send.allowed:
            return self._denied(send.decision_id, send.as_error())
        receive = self._evaluator.evaluate(
            PermissionEvaluationRequest(
                principal_id=message.receiver,
                capability="communication.message.receive",
                scope=PermissionScope(target=message.receiver, action="receive"),
                context={"message_type": message.message_type},
            )
        )
        if not receive.allowed:
            return CommunicationAuthorizationDecision(
                False,
                (send.decision_id, receive.decision_id),
                receive.as_error(),
            )
        return CommunicationAuthorizationDecision(
            True,
            (send.decision_id, receive.decision_id),
        )

    def authorize_publish(self, event: Event) -> CommunicationAuthorizationDecision:
        decision = self._evaluator.evaluate(
            PermissionEvaluationRequest(
                principal_id=event.publisher,
                capability="communication.event.publish",
                scope=PermissionScope(target=event.event_type, action="publish"),
                context={},
            )
        )
        if decision.allowed:
            return CommunicationAuthorizationDecision(True, (decision.decision_id,))
        return self._denied(decision.decision_id, decision.as_error())

    def authorize_consume(
        self,
        subscriber_id: str,
        event: Event,
    ) -> CommunicationAuthorizationDecision:
        decision = self._evaluator.evaluate(
            PermissionEvaluationRequest(
                principal_id=subscriber_id,
                capability="communication.event.consume",
                scope=PermissionScope(target=event.event_type, action="consume"),
                context={},
            )
        )
        if decision.allowed:
            return CommunicationAuthorizationDecision(True, (decision.decision_id,))
        return self._denied(decision.decision_id, decision.as_error())

    @staticmethod
    def _denied(decision_id: str, error: ErrorInfo) -> CommunicationAuthorizationDecision:
        return CommunicationAuthorizationDecision(False, (decision_id,), error)


__all__ = [
    "CommunicationAuthorizationDecision",
    "CommunicationAuthorizer",
    "PermissionCommunicationAuthorizer",
]
