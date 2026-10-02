"""Personal Department for ZYRO."""

from zyro.domains.personal.agents import register_personal_agents
from zyro.domains.personal.contracts import AgendaOverview, ReminderItem
from zyro.domains.personal.tools import PersonalAgendaHandler, PersonalReminderHandler

__all__ = [
    "AgendaOverview",
    "PersonalAgendaHandler",
    "PersonalOrganizerAgentHandler",
    "PersonalReminderHandler",
    "ReminderAgentHandler",
    "ReminderItem",
    "register_personal_agents",
]
