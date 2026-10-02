"""Transient bounded Context Assembly contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from zyro.core.data import validate_record, validate_text
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope


class ContextSource(StrEnum):
    USER_INPUT = "USER_INPUT"
    TASK_DATA = "TASK_DATA"
    CURRENT_STATE = "CURRENT_STATE"
    MEMORY = "MEMORY"
    KNOWLEDGE = "KNOWLEDGE"


@dataclass(frozen=True, slots=True)
class StateReference:
    category: str
    state_key: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "category", validate_text(self.category, "category", max_chars=256)
        )
        object.__setattr__(
            self, "state_key", validate_text(self.state_key, "state_key", max_chars=256)
        )


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_records: int = 20
    max_characters: int = 8_000
    max_sources: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.max_records <= 100:
            raise ValueError("context record budget must be between 1 and 100")
        if not 256 <= self.max_characters <= 65_536:
            raise ValueError("context character budget must be between 256 and 65536")
        if not 1 <= self.max_sources <= 5:
            raise ValueError("context source budget must be between 1 and 5")


@dataclass(frozen=True, slots=True)
class ContextRequest:
    requester_id: str
    task_id: str
    scope: ResourceScope
    query: str
    current_instruction: str
    task_data: Mapping[str, Any] = field(default_factory=dict)
    state_references: tuple[StateReference, ...] = ()
    include_memory: bool = True
    include_knowledge: bool = True
    budget: ContextBudget = field(default_factory=ContextBudget)

    def __post_init__(self) -> None:
        for name in ("requester_id", "task_id", "current_instruction"):
            object.__setattr__(
                self, name, validate_text(getattr(self, name), name, max_chars=8_000)
            )
        object.__setattr__(
            self, "query", validate_text(self.query, "context query", max_chars=2_000)
        )
        object.__setattr__(
            self, "task_data", validate_record(self.task_data, "task data", max_bytes=16_384)
        )
        if not isinstance(self.include_memory, bool) or not isinstance(
            self.include_knowledge, bool
        ):
            raise ValueError("context source flags must be booleans")


@dataclass(frozen=True, slots=True)
class ContextItem:
    source: ContextSource
    source_id: str
    scope: ResourceScope
    content: Mapping[str, Any]
    version: str
    provenance: str
    priority: int
    relevance: int
    selection_reason: str

    def __post_init__(self) -> None:
        for name in ("source_id", "version", "provenance", "selection_reason"):
            object.__setattr__(
                self, name, validate_text(getattr(self, name), name, max_chars=2_000)
            )
        object.__setattr__(
            self, "content", validate_record(self.content, "context item", max_bytes=65_536)
        )


@dataclass(frozen=True, slots=True)
class AssembledContext:
    task_id: str
    items: tuple[ContextItem, ...]
    used_characters: int
    truncated: bool
    error: ErrorInfo | None = None
    permission_decision_id: str | None = None


__all__ = [
    "AssembledContext",
    "ContextBudget",
    "ContextItem",
    "ContextRequest",
    "ContextSource",
    "StateReference",
]
