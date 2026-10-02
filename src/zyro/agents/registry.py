"""In-process Phase 2 registry for agent definitions and bounded handlers."""

from __future__ import annotations

from dataclasses import dataclass

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentHandler
from zyro.core.errors import InvalidAgentError, MissingAgentError


@dataclass(frozen=True, slots=True)
class RegisteredAgent:
    definition: AgentDefinition
    handler: AgentHandler


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, RegisteredAgent] = {}

    def register(self, definition: AgentDefinition, handler: AgentHandler) -> None:
        if definition.agent_id in self._agents:
            raise InvalidAgentError(f"agent is already registered: {definition.agent_id}")
        self._agents[definition.agent_id] = RegisteredAgent(definition, handler)

    def get(self, agent_id: str) -> RegisteredAgent:
        try:
            return self._agents[agent_id]
        except KeyError as error:
            raise MissingAgentError(f"agent is not registered: {agent_id}") from error
