"""Versioned reference-Knowledge contracts, distinct from retained Memory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from zyro.core.data import validate_record, validate_text
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope


class KnowledgeSourceType(StrEnum):
    DOCUMENT = "DOCUMENT"
    PROJECT_DOCUMENTATION = "PROJECT_DOCUMENTATION"
    DOMAIN_REFERENCE = "DOMAIN_REFERENCE"
    APPROVED_EXTERNAL_REFERENCE = "APPROVED_EXTERNAL_REFERENCE"


class KnowledgeStatus(StrEnum):
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    knowledge_id: str
    source_id: str
    source_type: KnowledgeSourceType
    source_reference: str
    scope: ResourceScope
    content: str
    chunk_index: int
    ingested_at: datetime
    version: str
    status: KnowledgeStatus
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("knowledge_id", "source_id", "source_reference", "version"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name, max_chars=1024))
        if not isinstance(self.source_type, KnowledgeSourceType):
            raise ValueError("source_type must be a KnowledgeSourceType")
        if not isinstance(self.scope, ResourceScope):
            raise ValueError("scope must be a ResourceScope")
        if not isinstance(self.status, KnowledgeStatus):
            raise ValueError("status must be a KnowledgeStatus")
        object.__setattr__(
            self, "content", validate_text(self.content, "knowledge content", max_chars=4_000)
        )
        if (
            not isinstance(self.chunk_index, int)
            or isinstance(self.chunk_index, bool)
            or self.chunk_index < 0
        ):
            raise ValueError("chunk index must be a non-negative integer")
        if not isinstance(self.ingested_at, datetime) or self.ingested_at.tzinfo is None:
            raise ValueError("ingested_at must be timezone-aware")
        object.__setattr__(
            self, "metadata", validate_record(self.metadata, "knowledge metadata", max_bytes=8_192)
        )


@dataclass(frozen=True, slots=True)
class KnowledgeIngestionRequest:
    requester_id: str
    source_id: str
    source_type: KnowledgeSourceType
    source_reference: str
    scope: ResourceScope
    content: str
    version: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("requester_id", "source_id", "source_reference", "version"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name, max_chars=1024))
        if not isinstance(self.source_type, KnowledgeSourceType):
            raise ValueError("source_type must be a KnowledgeSourceType")
        if not isinstance(self.scope, ResourceScope):
            raise ValueError("scope must be a ResourceScope")
        object.__setattr__(
            self, "content", validate_text(self.content, "knowledge content", max_chars=65_536)
        )
        object.__setattr__(
            self,
            "metadata",
            validate_record(self.metadata, "knowledge metadata", max_bytes=8_192),
        )


class KnowledgeIngestionOutcome(StrEnum):
    INGESTED = "INGESTED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"


@dataclass(frozen=True, slots=True)
class KnowledgeIngestionResult:
    outcome: KnowledgeIngestionOutcome
    records: tuple[KnowledgeRecord, ...] = ()
    error: ErrorInfo | None = None
    permission_decision_id: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeQuery:
    requester_id: str
    scope: ResourceScope
    query: str
    source_id: str | None = None
    current_only: bool = True
    limit: int = 20
    max_characters: int = 8_000

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "requester_id", validate_text(self.requester_id, "requester_id", max_chars=256)
        )
        if not isinstance(self.scope, ResourceScope):
            raise ValueError("scope must be a ResourceScope")
        if self.source_id is not None:
            object.__setattr__(
                self, "source_id", validate_text(self.source_id, "source_id", max_chars=1024)
            )
        if not isinstance(self.current_only, bool):
            raise ValueError("current_only must be a boolean")
        if not 1 <= self.limit <= 100 or not 1 <= self.max_characters <= 65_536:
            raise ValueError("knowledge retrieval budget is invalid")
        object.__setattr__(
            self, "query", validate_text(self.query, "knowledge query", max_chars=2_000)
        )


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalResult:
    records: tuple[KnowledgeRecord, ...]
    error: ErrorInfo | None = None
    permission_decision_id: str | None = None


__all__ = [
    "KnowledgeIngestionOutcome",
    "KnowledgeIngestionRequest",
    "KnowledgeIngestionResult",
    "KnowledgeQuery",
    "KnowledgeRecord",
    "KnowledgeRetrievalResult",
    "KnowledgeSourceType",
    "KnowledgeStatus",
]
