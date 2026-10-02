"""Tools for Personal operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from zyro.core.errors import ErrorInfo
from zyro.domains.personal.contracts import AgendaOverview, ReminderItem
from zyro.tools.contracts import ToolExecutionContext, ToolHandlerResult


class PersonalAgendaHandler:
    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        date_str = str(arguments.get("date", datetime.now(UTC).strftime("%Y-%m-%d")))
        overview = AgendaOverview(
            date_str=date_str,
            items=(
                "09:00 - 10:00: Deep Work (ZYRO Architecture & Engine)",
                "11:00 - 11:30: Weekly Priorities & Delivery Review",
                "14:00 - 15:00: Code Review & System Verification",
            ),
            focus_blocks=(
                "09:00 - 11:00: Focus block on coding core features",
                "15:00 - 17:00: Deep learning & verification audit",
            ),
        )
        return ToolHandlerResult.success(overview.to_dict())


class PersonalReminderHandler:
    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        title = str(arguments.get("title", "")).strip()
        if not title:
            return ToolHandlerResult.failure(
                ErrorInfo("missing_title", "title is required for reminder", "ValidationError")
            )
        due_at = datetime.now(UTC) + timedelta(hours=int(arguments.get("hours_from_now", 2)))
        priority = str(arguments.get("priority", "HIGH"))

        item = ReminderItem(
            reminder_id=f"rem-{uuid4().hex[:8]}",
            title=title,
            due_at=due_at,
            priority=priority,
        )
        return ToolHandlerResult.success(item.to_dict())


__all__ = ["PersonalAgendaHandler", "PersonalReminderHandler"]
