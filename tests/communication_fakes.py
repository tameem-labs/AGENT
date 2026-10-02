from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from zyro.communication.authorization import PermissionCommunicationAuthorizer
from zyro.communication.contracts import (
    DeliveryContext,
    DirectMessage,
    EventHandlerResult,
    MessageHandlerResult,
)
from zyro.core.errors import ErrorInfo
from zyro.core.events import Event
from zyro.security.permission import (
    Permission,
    PermissionEvaluator,
    PermissionScope,
    PermissionStore,
    PrincipalDirectory,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@dataclass
class MutableClock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def authorizer(
    grants: Iterable[tuple[str, str, str, str]],
) -> PermissionCommunicationAuthorizer:
    store = PermissionStore()
    principals: set[str] = set()
    capabilities: set[str] = set()
    sequence = count(1)
    for principal, capability, target, action in grants:
        principals.add(principal)
        capabilities.add(capability)
        store.add(
            Permission(
                permission_id=f"permission-{next(sequence)}",
                principal_id=principal,
                capability=capability,
                scope=PermissionScope(target=target, action=action),
                policy_version="communication-v1",
                issued_at=NOW - timedelta(minutes=1),
            )
        )
    evaluator = PermissionEvaluator(
        store,
        PrincipalDirectory(tuple(sorted(principals))),
        frozenset(capabilities),
        policy_version="communication-v1",
        clock=lambda: NOW,
        id_factory=lambda: f"decision-{next(sequence)}",
    )
    return PermissionCommunicationAuthorizer(evaluator)


def full_authorizer(*, event_type: str = "LEAD_QUALIFIED") -> PermissionCommunicationAuthorizer:
    return authorizer(
        (
            ("sender-1", "communication.message.send", "receiver-1", "send"),
            ("receiver-1", "communication.message.receive", "receiver-1", "receive"),
            (
                "freelancing.qualification-pipeline",
                "communication.event.publish",
                event_type,
                "publish",
            ),
            ("subscriber-a", "communication.event.consume", event_type, "consume"),
            ("subscriber-b", "communication.event.consume", event_type, "consume"),
        )
    )


@dataclass
class RecordingMessageHandler:
    results: list[MessageHandlerResult | BaseException]
    calls: list[DirectMessage] = field(default_factory=list)

    def handle(self, message: DirectMessage) -> MessageHandlerResult:
        self.calls.append(message)
        selected = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(selected, BaseException):
            raise selected
        return selected


@dataclass
class RecordingEventHandler:
    results: list[EventHandlerResult | BaseException]
    calls: list[tuple[Event, DeliveryContext]] = field(default_factory=list)

    def handle(self, event: Event, context: DeliveryContext) -> EventHandlerResult:
        self.calls.append((event, context))
        selected = self.results[min(len(self.calls) - 1, len(self.results) - 1)]
        if isinstance(selected, BaseException):
            raise selected
        return selected


def event_success(*, acknowledged: bool = True) -> EventHandlerResult:
    return EventHandlerResult(True, acknowledged=acknowledged)


def event_failure(*, retryable: bool = True) -> EventHandlerResult:
    return EventHandlerResult(
        False,
        error=ErrorInfo("fixture_failure", "Fixture delivery failed.", "FixtureFailure"),
        retryable=retryable,
    )


def response(value: Mapping[str, Any] | None = None) -> MessageHandlerResult:
    return MessageHandlerResult(True, value)
