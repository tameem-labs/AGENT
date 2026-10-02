"""Versioned durable Memory contracts; Memory is history, not live State."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from zyro.core.data import validate_record, validate_text
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope, ScopeKind


class MemoryLayer(StrEnum):
    WORKING = "WORKING"
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PROCEDURAL = "PROCEDURAL"
    PROJECT = "PROJECT"
    CORE = "CORE"
    ARCHIVE = "ARCHIVE"


class MemoryType(StrEnum):
    PREFERENCE = "PREFERENCE"
    DECISION = "DECISION"
    RELATIONSHIP = "RELATIONSHIP"
    HISTORICAL_OUTCOME = "HISTORICAL_OUTCOME"
    EVENT = "EVENT"
    PROCEDURE = "PROCEDURE"
    NOTE = "NOTE"


class AssertionType(StrEnum):
    FACT = "FACT"
    INFERENCE = "INFERENCE"


class MemorySourceType(StrEnum):
    USER_INPUT = "USER_INPUT"
    SYSTEM_EVENT = "SYSTEM_EVENT"
    TASK_RESULT = "TASK_RESULT"
    VERIFIED_OUTCOME = "VERIFIED_OUTCOME"
    IMPORTED_DOCUMENT = "IMPORTED_DOCUMENT"
    AGENT_OUTPUT = "AGENT_OUTPUT"
    EXTERNAL_SOURCE = "EXTERNAL_SOURCE"
    OWNER_ENTRY = "OWNER_ENTRY"


class PrivacyClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    PRIVATE = "PRIVATE"
    RESTRICTED = "RESTRICTED"


class MemoryStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CONTRADICTED = "CONTRADICTED"
    FORGOTTEN = "FORGOTTEN"


class MemoryWriteReason(StrEnum):
    USER_INSTRUCTION = "USER_INSTRUCTION"
    VERIFIED_TASK_OUTCOME = "VERIFIED_TASK_OUTCOME"
    PROJECT_DECISION = "PROJECT_DECISION"
    STABLE_PREFERENCE = "STABLE_PREFERENCE"
    RELATIONSHIP_INFORMATION = "RELATIONSHIP_INFORMATION"
    HISTORICAL_OUTCOME = "HISTORICAL_OUTCOME"
    OPERATIONAL_REQUIREMENT = "OPERATIONAL_REQUIREMENT"


@dataclass(frozen=True, slots=True)
class MemoryProvenance:
    source_type: MemorySourceType
    source_id: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, MemorySourceType):
            raise ValueError("source_type must be a MemorySourceType")
        object.__setattr__(
            self, "source_id", validate_text(self.source_id, "source_id", max_chars=512)
        )
        evidence = tuple(
            validate_text(item, "evidence_id", max_chars=512) for item in self.evidence_ids
        )
        if len(set(evidence)) != len(evidence):
            raise ValueError("evidence identifiers must be unique")
        object.__setattr__(self, "evidence_ids", evidence)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    expires_at: datetime | None = None
    owner_controlled: bool = False

    def __post_init__(self) -> None:
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("retention expiry must be timezone-aware")
        if not isinstance(self.owner_controlled, bool):
            raise ValueError("owner_controlled must be a boolean")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    logical_key: str
    scope: ResourceScope
    layer: MemoryLayer
    memory_type: MemoryType
    assertion_type: AssertionType
    content: Mapping[str, Any]
    provenance: MemoryProvenance
    created_at: datetime
    updated_at: datetime
    valid_from: datetime | None
    valid_until: datetime | None
    confidence: float
    status: MemoryStatus
    retention: RetentionPolicy
    privacy: PrivacyClassification
    revision: int
    supersedes_memory_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("memory_id", "logical_key"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name, max_chars=512))
        if not isinstance(self.scope, ResourceScope):
            raise ValueError("scope must be a ResourceScope")
        for value, kind, name in (
            (self.layer, MemoryLayer, "layer"),
            (self.memory_type, MemoryType, "memory_type"),
            (self.assertion_type, AssertionType, "assertion_type"),
            (self.status, MemoryStatus, "status"),
            (self.privacy, PrivacyClassification, "privacy"),
        ):
            if not isinstance(value, kind):
                raise ValueError(f"{name} has an invalid type")
        if not isinstance(self.provenance, MemoryProvenance):
            raise ValueError("provenance must be a MemoryProvenance")
        if not isinstance(self.retention, RetentionPolicy):
            raise ValueError("retention must be a RetentionPolicy")
        for timestamp in (self.created_at, self.updated_at):
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                raise ValueError("memory timestamps must be timezone-aware")
        if self.valid_from is not None and self.valid_from.tzinfo is None:
            raise ValueError("valid_from must be timezone-aware")
        if self.valid_until is not None and self.valid_until.tzinfo is None:
            raise ValueError("valid_until must be timezone-aware")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("valid_until must be after valid_from")
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be a finite number between zero and one")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
        ):
            raise ValueError("memory revision must be positive")
        object.__setattr__(self, "content", validate_record(self.content, "memory content"))
        if self.supersedes_memory_id is not None:
            object.__setattr__(
                self,
                "supersedes_memory_id",
                validate_text(self.supersedes_memory_id, "supersedes_memory_id", max_chars=512),
            )

    def effective(self, now: datetime) -> bool:
        return (
            self.status is MemoryStatus.ACTIVE
            and (self.valid_from is None or now >= self.valid_from)
            and (self.valid_until is None or now < self.valid_until)
            and (self.retention.expires_at is None or now < self.retention.expires_at)
        )


@dataclass(frozen=True, slots=True)
class MemoryWriteRequest:
    requester_id: str
    memory_id: str
    logical_key: str
    scope: ResourceScope
    layer: MemoryLayer
    memory_type: MemoryType
    assertion_type: AssertionType
    content: Mapping[str, Any]
    provenance: MemoryProvenance
    reason: MemoryWriteReason
    privacy: PrivacyClassification = PrivacyClassification.PRIVATE
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    retention: RetentionPolicy | None = None

    def __post_init__(self) -> None:
        for name, size in (("requester_id", 256), ("memory_id", 512), ("logical_key", 512)):
            object.__setattr__(self, name, validate_text(getattr(self, name), name, max_chars=size))
        if not isinstance(self.scope, ResourceScope):
            raise ValueError("scope must be a ResourceScope")
        for value, kind, name in (
            (self.layer, MemoryLayer, "layer"),
            (self.memory_type, MemoryType, "memory_type"),
            (self.assertion_type, AssertionType, "assertion_type"),
            (self.reason, MemoryWriteReason, "reason"),
            (self.privacy, PrivacyClassification, "privacy"),
        ):
            if not isinstance(value, kind):
                raise ValueError(f"{name} has an invalid type")
        if not isinstance(self.provenance, MemoryProvenance):
            raise ValueError("provenance must be a MemoryProvenance")
        if self.retention is not None and not isinstance(self.retention, RetentionPolicy):
            raise ValueError("retention must be a RetentionPolicy")
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be a finite number between zero and one")
        if self.valid_from is not None and self.valid_from.tzinfo is None:
            raise ValueError("valid_from must be timezone-aware")
        if self.valid_until is not None and self.valid_until.tzinfo is None:
            raise ValueError("valid_until must be timezone-aware")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("valid_until must be after valid_from")
        object.__setattr__(self, "content", validate_record(self.content, "memory content"))


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    requester_id: str
    scope: ResourceScope
    query: str
    layers: tuple[MemoryLayer, ...] = ()
    memory_types: tuple[MemoryType, ...] = ()
    since: datetime | None = None
    limit: int = 20
    max_characters: int = 8_000

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "requester_id", validate_text(self.requester_id, "requester_id", max_chars=256)
        )
        if not isinstance(self.scope, ResourceScope):
            raise ValueError("scope must be a ResourceScope")
        if any(not isinstance(item, MemoryLayer) for item in self.layers):
            raise ValueError("layers must contain only MemoryLayer values")
        if any(not isinstance(item, MemoryType) for item in self.memory_types):
            raise ValueError("memory_types must contain only MemoryType values")
        if self.since is not None and self.since.tzinfo is None:
            raise ValueError("since must be timezone-aware")
        if not 1 <= self.limit <= 100:
            raise ValueError("memory query limit must be between 1 and 100")
        if not 1 <= self.max_characters <= 65_536:
            raise ValueError("memory character budget must be between 1 and 65536")
        object.__setattr__(
            self, "query", validate_text(self.query, "memory query", max_chars=2_000)
        )


class MemoryWriteOutcome(StrEnum):
    CREATED = "CREATED"
    DUPLICATE = "DUPLICATE"
    CORRECTED = "CORRECTED"
    FORGOTTEN = "FORGOTTEN"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    outcome: MemoryWriteOutcome
    record: MemoryRecord | None = None
    error: ErrorInfo | None = None
    permission_decision_id: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryRetrievalResult:
    records: tuple[MemoryRecord, ...]
    error: ErrorInfo | None = None
    permission_decision_ids: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "AssertionType",
    "MemoryLayer",
    "MemoryProvenance",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryRetrievalResult",
    "MemorySourceType",
    "MemoryStatus",
    "MemoryType",
    "MemoryWriteOutcome",
    "MemoryWriteReason",
    "MemoryWriteRequest",
    "MemoryWriteResult",
    "PrivacyClassification",
    "ResourceScope",
    "RetentionPolicy",
    "ScopeKind",
]
