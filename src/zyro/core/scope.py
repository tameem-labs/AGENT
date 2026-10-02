"""Shared resource scope identity without storage or authority semantics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from zyro.core.data import validate_text


class ScopeKind(StrEnum):
    USER = "USER"
    PROJECT = "PROJECT"
    TASK = "TASK"
    WORKFLOW = "WORKFLOW"
    AGENT = "AGENT"
    DOMAIN = "DOMAIN"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True, slots=True)
class ResourceScope:
    kind: ScopeKind
    scope_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ScopeKind):
            raise ValueError("scope kind must be a ScopeKind")
        object.__setattr__(
            self, "scope_id", validate_text(self.scope_id, "scope_id", max_chars=256)
        )

    @property
    def target(self) -> str:
        return f"{self.kind.value}:{self.scope_id}"


__all__ = ["ResourceScope", "ScopeKind"]
