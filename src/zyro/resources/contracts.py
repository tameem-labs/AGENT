"""Local Resource Manager admission, lease, usage, and hard-limit contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from zyro.core.data import validate_text

DEFAULT_TASK_TOKEN_LIMIT = 50_000
DEFAULT_WORKFLOW_TOKEN_LIMIT = 300_000


class ResourceKind(StrEnum):
    TASK_SLOT = "TASK_SLOT"
    AGENT_SLOT = "AGENT_SLOT"
    TOOL_CALL = "TOOL_CALL"


class WorkLane(StrEnum):
    INTERACTIVE = "INTERACTIVE"
    BACKGROUND = "BACKGROUND"


class ReservationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    QUEUED = "QUEUED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class AdmissionOutcome(StrEnum):
    ADMITTED = "ADMITTED"
    QUEUED = "QUEUED"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    max_requests: int
    window_seconds: float

    def __post_init__(self) -> None:
        if self.max_requests < 1 or self.window_seconds <= 0:
            raise ValueError("rate limit policy must be positive")


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    task_token_limit: int = DEFAULT_TASK_TOKEN_LIMIT
    workflow_token_limit: int = DEFAULT_WORKFLOW_TOKEN_LIMIT
    max_concurrent_tasks: int = 8
    max_concurrent_agents: int = 8
    max_concurrent_tool_calls: int = 4
    default_lease_seconds: float = 60.0
    background_fairness_seconds: float = 30.0
    resource_limits: Mapping[str, int] = field(default_factory=dict)
    rate_limits: Mapping[str, RateLimitPolicy] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "task_token_limit",
            "workflow_token_limit",
            "max_concurrent_tasks",
            "max_concurrent_agents",
            "max_concurrent_tool_calls",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.default_lease_seconds <= 0 or self.background_fairness_seconds < 0:
            raise ValueError("lease and fairness durations are invalid")
        for key, value in self.resource_limits.items():
            validate_text(key, "resource limit key")
            if value < 1:
                raise ValueError("resource limits must be positive")
        for key, value in self.rate_limits.items():
            validate_text(key, "rate limit key")
            if not isinstance(value, RateLimitPolicy):
                raise ValueError("rate limits must contain RateLimitPolicy values")
        object.__setattr__(self, "resource_limits", MappingProxyType(dict(self.resource_limits)))
        object.__setattr__(self, "rate_limits", MappingProxyType(dict(self.rate_limits)))

    def concurrency_limit(self, kind: ResourceKind, resource_id: str) -> int:
        exact = f"{kind.value}:{resource_id}"
        if exact in self.resource_limits:
            return self.resource_limits[exact]
        return {
            ResourceKind.TASK_SLOT: self.max_concurrent_tasks,
            ResourceKind.AGENT_SLOT: self.max_concurrent_agents,
            ResourceKind.TOOL_CALL: self.max_concurrent_tool_calls,
        }[kind]


@dataclass(frozen=True, slots=True)
class ResourceReservation:
    reservation_id: str
    owner_id: str
    resource_kind: ResourceKind
    resource_id: str
    lane: WorkLane
    task_id: str
    workflow_id: str | None
    status: ReservationStatus
    acquired_at: datetime | None
    expires_at: datetime | None
    queued_at: datetime


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    outcome: AdmissionOutcome
    reservation: ResourceReservation
    limit: int


class UsagePrecision(StrEnum):
    EXACT = "EXACT"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class ConsumptionOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    RESOURCE_LIMIT_REACHED = "RESOURCE_LIMIT_REACHED"
    UNKNOWN_RECORDED = "UNKNOWN_RECORDED"


@dataclass(frozen=True, slots=True)
class TokenConsumptionResult:
    outcome: ConsumptionOutcome
    task_id: str
    workflow_id: str | None
    units: int | None
    precision: UsagePrecision
    task_used: int
    task_limit: int
    workflow_used: int | None
    workflow_limit: int | None
    remaining_task: int
    remaining_workflow: int | None


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    resource_id: str
    limit: int
    used: int
    retry_after_seconds: float | None = None


__all__ = [
    "DEFAULT_TASK_TOKEN_LIMIT",
    "DEFAULT_WORKFLOW_TOKEN_LIMIT",
    "AdmissionOutcome",
    "AdmissionResult",
    "ConsumptionOutcome",
    "RateLimitPolicy",
    "RateLimitResult",
    "ReservationStatus",
    "ResourceKind",
    "ResourcePolicy",
    "ResourceReservation",
    "TokenConsumptionResult",
    "UsagePrecision",
    "WorkLane",
]
