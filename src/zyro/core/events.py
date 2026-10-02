"""Minimal in-process event publication seam; this is not a durable event bus."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clean(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class EventDelivery:
    durable: bool = False
    ack_required: bool = False
    ordering_key: str | None = None
    retry_policy: str = "none"

    def __post_init__(self) -> None:
        if self.durable or self.ack_required or self.retry_policy != "none":
            raise ValueError("the in-process event seam cannot claim durable delivery")
        if self.ordering_key is not None:
            object.__setattr__(
                self,
                "ordering_key",
                _clean(self.ordering_key, "ordering_key"),
            )


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    request_id: str
    task_id: str
    correlation_id: str
    event_type: str
    publisher: str
    payload: Mapping[str, Any]
    timestamp: datetime
    version: str
    workflow_id: str | None = None
    delivery: EventDelivery = field(default_factory=EventDelivery)

    def __post_init__(self) -> None:
        for field_name in (
            "event_id",
            "request_id",
            "task_id",
            "correlation_id",
            "event_type",
            "publisher",
            "version",
        ):
            object.__setattr__(self, field_name, _clean(getattr(self, field_name), field_name))
        if self.workflow_id is not None:
            object.__setattr__(self, "workflow_id", _clean(self.workflow_id, "workflow_id"))
        if self.timestamp.tzinfo is None:
            raise ValueError("event timestamp must be timezone-aware")
        object.__setattr__(self, "payload", _freeze(self.payload))


class EventPublisher(Protocol):
    def publish(self, event: Event, *, idempotency_key: str) -> bool:
        """Publish once; return False when the idempotency key was already published."""
        ...


class InProcessEventPublisher:
    """Bounded deterministic publication record without delivery or subscription behavior."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._idempotency_keys: set[str] = set()

    def publish(self, event: Event, *, idempotency_key: str) -> bool:
        key = _clean(idempotency_key, "idempotency_key")
        if key in self._idempotency_keys:
            return False
        self._idempotency_keys.add(key)
        self._events.append(event)
        return True

    def events(self, event_type: str | None = None) -> tuple[Event, ...]:
        return tuple(
            event for event in self._events if event_type is None or event.event_type == event_type
        )


def new_event_id(factory: Callable[[], str] | None = None) -> str:
    return (factory or (lambda: str(uuid4())))()


__all__ = [
    "Event",
    "EventDelivery",
    "EventPublisher",
    "InProcessEventPublisher",
    "new_event_id",
]
