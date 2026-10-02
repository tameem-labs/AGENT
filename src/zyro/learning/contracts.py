"""Contracts for ZYRO Long-Term Learning and Memory Consolidation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LearningPatternType(StrEnum):
    PREFERENCE = "PREFERENCE"
    CORRECTION = "CORRECTION"
    WORKFLOW_SUCCESS = "WORKFLOW_SUCCESS"
    WORKFLOW_FAILURE = "WORKFLOW_FAILURE"


@dataclass(frozen=True, slots=True)
class ExtractedPreference:
    """A preference candidate extracted conservatively from dialogue or interaction."""

    key: str
    value: str
    confidence: float
    is_correction: bool
    source_text: str
    timestamp: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class WorkflowExperience:
    """Record of an executed workflow for pattern learning and recovery insights."""

    workflow_id: str
    intent: str
    outcome: str  # "SUCCESS", "FAILURE", "PARTIAL"
    duration_seconds: float
    error_message: str | None = None
    step_count: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class ConsolidationReport:
    """Result of a memory consolidation pass."""

    examined_count: int
    superseded_count: int
    contradicted_count: int
    active_retained_count: int
    timestamp: datetime = field(default_factory=_utc_now)


__all__ = [
    "ConsolidationReport",
    "ExtractedPreference",
    "LearningPatternType",
    "WorkflowExperience",
]
