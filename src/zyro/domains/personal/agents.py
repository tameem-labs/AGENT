"""Agent handlers for the Personal Department."""

from __future__ import annotations

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.risk import RiskClass


class PersonalOrganizerAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool("personal.list_agenda", {})
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        items = output.get("items", [])
        summary = f"Retrieved agenda for {output.get('date')} with {len(items)} scheduled items."
        return AgentExecution.success(
            {"message": summary, "agenda": dict(output)},
            tool_results=(tool_res,),
        )


class ReminderAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool("personal.create_reminder", {"title": context.goal})
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        summary = f"Reminder created: '{output.get('title')}' due at {output.get('due_at')}."
        return AgentExecution.success(
            {"message": summary, "reminder": dict(output)},
            tool_results=(tool_res,),
        )


def register_personal_agents(registry: AgentRegistry) -> None:
    organizer_def = AgentDefinition(
        "personal.organizer",
        "Personal Organizer Agent",
        "1.0.0",
        "Coordinate daily schedule, priorities, and focus blocks",
        "personal",
        ("agenda organization", "calendar planning"),
        ("personal.organize",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(organizer_def, PersonalOrganizerAgentHandler())

    reminder_def = AgentDefinition(
        "personal.reminders",
        "Personal Reminders Agent",
        "1.0.0",
        "Manage reminders, deadlines, and task alerts",
        "personal",
        ("reminders", "alerts"),
        ("personal.remind",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(reminder_def, ReminderAgentHandler())


__all__ = [
    "PersonalOrganizerAgentHandler",
    "ReminderAgentHandler",
    "register_personal_agents",
]
