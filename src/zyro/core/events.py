"""Canonical event envelope and bounded in-process publication compatibility seam."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol, cast
from uuid import uuid4

MAX_PAYLOAD_BYTES = 65_536
_SENSITIVE_NAMES = {
    "secret",
    "password",
    "credential",
    "api_key",
    "access_token",
    "private_key",
    "refresh_token",
}
_SECRET_TEXT = re.compile(
    r"(?i)\b(password|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"private[_-]?key|credential)\b\s*[:=]"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clean(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def validate_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Reject secret-shaped keys, unsupported values, and oversized envelopes."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")

    def inspect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError("payload keys must be non-empty strings")
                if key.lower().replace("-", "_") in _SENSITIVE_NAMES:
                    raise ValueError("payload cannot contain secret fields")
                inspect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                inspect(item)
        elif isinstance(value, str) and _SECRET_TEXT.search(value):
            raise ValueError("payload cannot contain secret-shaped assignments")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("payload numbers must be finite")
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("payload values must be JSON-compatible")

    inspect(payload)
    try:
        encoded = json.dumps(_plain(payload), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("payload must be deterministically serializable") from error
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds the bounded envelope size")
    return cast(Mapping[str, Any], _freeze(payload))


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_attempts, int)
            or isinstance(self.max_attempts, bool)
            or not 1 <= self.max_attempts <= 100
        ):
            raise ValueError("max_attempts must be an integer between 1 and 100")
        if (
            not isinstance(self.backoff_seconds, (int, float))
            or isinstance(self.backoff_seconds, bool)
            or not math.isfinite(self.backoff_seconds)
            or self.backoff_seconds < 0
        ):
            raise ValueError("backoff_seconds must be a finite non-negative number")


@dataclass(frozen=True, slots=True)
class EventDelivery:
    durable: bool = False
    ack_required: bool = False
    ordering_key: str | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.durable, bool) or not isinstance(self.ack_required, bool):
            raise ValueError("delivery durable and ack_required flags must be booleans")
        if not isinstance(self.retry_policy, RetryPolicy):
            raise ValueError("retry_policy must be a RetryPolicy")
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
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ValueError("event timestamp must be a timezone-aware datetime")
        if not isinstance(self.delivery, EventDelivery):
            raise ValueError("delivery must be an EventDelivery")
        object.__setattr__(self, "payload", validate_payload(self.payload))

    def to_json(self) -> str:
        document = {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "workflow_id": self.workflow_id,
            "correlation_id": self.correlation_id,
            "event_type": self.event_type,
            "publisher": self.publisher,
            "payload": _plain(self.payload),
            "delivery": {
                "durable": self.delivery.durable,
                "ack_required": self.delivery.ack_required,
                "ordering_key": self.delivery.ordering_key,
                "retry_policy": {
                    "max_attempts": self.delivery.retry_policy.max_attempts,
                    "backoff_seconds": self.delivery.retry_policy.backoff_seconds,
                },
            },
            "timestamp": self.timestamp.isoformat(),
            "version": self.version,
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, encoded: str) -> Event:
        try:
            document = json.loads(encoded)
            delivery = document["delivery"]
            retry = delivery["retry_policy"]
            return cls(
                event_id=document["event_id"],
                request_id=document["request_id"],
                task_id=document["task_id"],
                workflow_id=document["workflow_id"],
                correlation_id=document["correlation_id"],
                event_type=document["event_type"],
                publisher=document["publisher"],
                payload=document["payload"],
                delivery=EventDelivery(
                    durable=delivery["durable"],
                    ack_required=delivery["ack_required"],
                    ordering_key=delivery["ordering_key"],
                    retry_policy=RetryPolicy(
                        max_attempts=retry["max_attempts"],
                        backoff_seconds=retry["backoff_seconds"],
                    ),
                ),
                timestamp=datetime.fromisoformat(document["timestamp"]),
                version=document["version"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid serialized event envelope") from error


class EventPublisher(Protocol):
    def publish(self, event: Event, *, idempotency_key: str) -> bool: ...


class InProcessEventPublisher:
    """Compatibility recorder without durable event bus behavior."""

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
    "RetryPolicy",
    "new_event_id",
    "validate_payload",
]
