"""Contracts for the Personal Department."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from zyro.core.data import validate_text


@dataclass(frozen=True, slots=True)
class ReminderItem:
    reminder_id: str
    title: str
    due_at: datetime
    priority: str
    completed: bool = False

    def __post_init__(self) -> None:
        for name in ("reminder_id", "title", "priority"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "reminder_id": self.reminder_id,
            "title": self.title,
            "due_at": self.due_at.astimezone(UTC).isoformat(),
            "priority": self.priority,
            "completed": self.completed,
        }


@dataclass(frozen=True, slots=True)
class AgendaOverview:
    date_str: str
    items: tuple[str, ...]
    focus_blocks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date_str,
            "items": list(self.items),
            "focus_blocks": list(self.focus_blocks),
        }


__all__ = ["AgendaOverview", "ReminderItem"]
