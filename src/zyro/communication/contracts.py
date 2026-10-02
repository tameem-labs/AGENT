"""Direct-message and durable-delivery contracts kept distinct from events."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from zyro.core.errors import ErrorInfo
from zyro.core.events import Event, validate_payload


def _clean(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


class MessagePriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class DirectMessage:
    message_id: str
    request_id: str
    task_id: str
    workflow_id: str | None
    correlation_id: str
    sender: str
    receiver: str
    message_type: str
    priority: MessagePriority
    payload: Mapping[str, Any]
    response_required: bool
    timestamp: datetime
    version: str

    def __post_init__(self) -> None:
        for field_name in (
            "message_id",
            "request_id",
            "task_id",
            "correlation_id",
            "sender",
            "receiver",
            "message_type",
            "version",
        ):
            object.__setattr__(self, field_name, _clean(getattr(self, field_name), field_name))
        if self.workflow_id is not None:
            object.__setattr__(self, "workflow_id", _clean(self.workflow_id, "workflow_id"))
        if not isinstance(self.priority, MessagePriority):
            raise ValueError("priority must be a MessagePriority")
        if not isinstance(self.response_required, bool):
            raise ValueError("response_required must be a boolean")
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ValueError("message timestamp must be a timezone-aware datetime")
        object.__setattr__(self, "payload", validate_payload(self.payload))

    def to_json(self) -> str:
        return json.dumps(
            {
                "message_id": self.message_id,
                "request_id": self.request_id,
                "task_id": self.task_id,
                "workflow_id": self.workflow_id,
                "correlation_id": self.correlation_id,
                "sender": self.sender,
                "receiver": self.receiver,
                "message_type": self.message_type,
                "priority": self.priority.value,
                "payload": _plain(self.payload),
                "response_required": self.response_required,
                "timestamp": self.timestamp.isoformat(),
                "version": self.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, encoded: str) -> DirectMessage:
        try:
            document = json.loads(encoded)
            return cls(
                message_id=document["message_id"],
                request_id=document["request_id"],
                task_id=document["task_id"],
                workflow_id=document["workflow_id"],
                correlation_id=document["correlation_id"],
                sender=document["sender"],
                receiver=document["receiver"],
                message_type=document["message_type"],
                priority=MessagePriority(document["priority"]),
                payload=document["payload"],
                response_required=document["response_required"],
                timestamp=datetime.fromisoformat(document["timestamp"]),
                version=document["version"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid serialized direct-message envelope") from error


@dataclass(frozen=True, slots=True)
class MessageDeliveryPolicy:
    max_attempts: int = 1
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_attempts, int)
            or isinstance(self.max_attempts, bool)
            or not 1 <= self.max_attempts <= 100
        ):
            raise ValueError("max_attempts must be an integer between 1 and 100")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number")


@dataclass(frozen=True, slots=True)
class MessageHandlerResult:
    acknowledged: bool
    response: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.acknowledged, bool):
            raise ValueError("acknowledged must be a boolean")
        if self.response is not None:
            object.__setattr__(self, "response", validate_payload(self.response))


class MessageHandler(Protocol):
    def handle(self, message: DirectMessage) -> MessageHandlerResult: ...


class MessageDeliveryStatus(StrEnum):
    ACKED = "ACKED"
    DUPLICATE = "DUPLICATE"
    UNAUTHORIZED = "UNAUTHORIZED"
    UNKNOWN_RECEIVER = "UNKNOWN_RECEIVER"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"


@dataclass(frozen=True, slots=True)
class MessageDeliveryResult:
    status: MessageDeliveryStatus
    message_id: str
    request_id: str
    task_id: str
    correlation_id: str
    receiver: str
    attempts: int
    response: Mapping[str, Any] | None = None
    error: ErrorInfo | None = None
    permission_decision_ids: tuple[str, ...] = ()

    @property
    def acknowledged(self) -> bool:
        return self.status is MessageDeliveryStatus.ACKED


@dataclass(frozen=True, slots=True)
class EventHandlerResult:
    succeeded: bool
    acknowledged: bool = False
    retryable: bool = True
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, bool) for value in (self.succeeded, self.acknowledged, self.retryable)
        ):
            raise ValueError("handler outcome flags must be booleans")
        if self.succeeded and self.error is not None:
            raise ValueError("successful handler result cannot contain an error")
        if not self.succeeded and self.error is None:
            raise ValueError("failed handler result requires a structured error")


@dataclass(frozen=True, slots=True)
class DeliveryContext:
    delivery_id: str
    attempt_id: str
    subscriber_id: str
    attempt: int


class EventHandler(Protocol):
    def handle(self, event: Event, context: DeliveryContext) -> EventHandlerResult: ...


@dataclass(frozen=True, slots=True)
class EventSubscription:
    subscriber_id: str
    event_types: tuple[str, ...]
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "subscriber_id", _clean(self.subscriber_id, "subscriber_id"))
        object.__setattr__(
            self,
            "event_types",
            tuple(_clean(item, "event_type") for item in self.event_types),
        )
        if not self.event_types or len(set(self.event_types)) != len(self.event_types):
            raise ValueError("event_types must be non-empty and unique")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")


class EventPublishStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    INVALID = "INVALID"
    UNAUTHORIZED = "UNAUTHORIZED"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"


@dataclass(frozen=True, slots=True)
class EventPublicationResult:
    status: EventPublishStatus
    event_id: str
    permission_decision_ids: tuple[str, ...] = ()
    error: ErrorInfo | None = None

    @property
    def accepted(self) -> bool:
        return self.status is EventPublishStatus.ACCEPTED


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERING = "DELIVERING"
    RETRY_WAIT = "RETRY_WAIT"
    ACKED = "ACKED"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True, slots=True)
class EventAcknowledgement:
    event_id: str
    subscriber_id: str
    attempt_id: str
    acknowledged_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("event_id", "subscriber_id", "attempt_id"):
            object.__setattr__(self, field_name, _clean(getattr(self, field_name), field_name))
        if self.acknowledged_at.tzinfo is None:
            raise ValueError("acknowledgement timestamp must be timezone-aware")


class AcknowledgementOutcome(StrEnum):
    ACKED = "ACKED"
    DUPLICATE = "DUPLICATE"
    INVALID = "INVALID"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    delivery_id: str
    event_id: str
    subscriber_id: str
    status: DeliveryStatus
    attempts: int
    attempt_id: str | None
    available_at: datetime
    updated_at: datetime
    last_error_code: str | None = None
    last_error_type: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryAttemptRecord:
    attempt_id: str
    delivery_id: str
    attempt: int
    status: str
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class DeadLetterRecord:
    delivery_id: str
    event_id: str
    subscriber_id: str
    attempts: int
    failed_at: datetime
    error_code: str
    error_type: str
    request_id: str
    task_id: str
    workflow_id: str | None
    correlation_id: str


@dataclass(frozen=True, slots=True)
class DeliveryCycleResult:
    delivery_id: str
    event_id: str
    subscriber_id: str
    status: DeliveryStatus
    attempt: int
    error: ErrorInfo | None = None


__all__ = [
    "AcknowledgementOutcome",
    "DeadLetterRecord",
    "DeliveryAttemptRecord",
    "DeliveryContext",
    "DeliveryCycleResult",
    "DeliveryRecord",
    "DeliveryStatus",
    "DirectMessage",
    "EventAcknowledgement",
    "EventHandler",
    "EventHandlerResult",
    "EventPublicationResult",
    "EventPublishStatus",
    "EventSubscription",
    "MessageDeliveryPolicy",
    "MessageDeliveryResult",
    "MessageDeliveryStatus",
    "MessageHandler",
    "MessageHandlerResult",
    "MessagePriority",
]
