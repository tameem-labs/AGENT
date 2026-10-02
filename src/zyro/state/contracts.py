"""Durable current-State contracts with explicit ownership and revisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from zyro.core.data import validate_record, validate_text
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope


@dataclass(frozen=True, slots=True)
class StateRecord:
    category: str
    state_key: str
    scope: ResourceScope
    owner_id: str
    value: Mapping[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ("category", "state_key", "owner_id"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name, max_chars=256))
        if not isinstance(self.scope, ResourceScope):
            raise ValueError("scope must be a ResourceScope")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
        ):
            raise ValueError("state revision must be a positive integer")
        if (
            not isinstance(self.created_at, datetime)
            or not isinstance(self.updated_at, datetime)
            or self.created_at.tzinfo is None
            or self.updated_at.tzinfo is None
        ):
            raise ValueError("state timestamps must be timezone-aware")
        object.__setattr__(self, "value", validate_record(self.value, "state value"))


class StateWriteOutcome(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    STALE = "STALE"
    UNAUTHORIZED = "UNAUTHORIZED"
    UNKNOWN_CATEGORY = "UNKNOWN_CATEGORY"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class StateWriteResult:
    outcome: StateWriteOutcome
    record: StateRecord | None = None
    error: ErrorInfo | None = None


@dataclass(frozen=True, slots=True)
class StateReadResult:
    record: StateRecord | None
    error: ErrorInfo | None = None
    permission_decision_id: str | None = None


__all__ = ["StateReadResult", "StateRecord", "StateWriteOutcome", "StateWriteResult"]
