"""Deterministic in-process registry for bounded tools and handlers."""

from __future__ import annotations

from dataclasses import dataclass

from zyro.tools.contracts import ToolDefinition, ToolHandler
from zyro.tools.errors import DuplicateToolError, MissingToolError


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.tool_id in self._tools:
            raise DuplicateToolError(f"tool is already registered: {definition.tool_id}")
        self._tools[definition.tool_id] = RegisteredTool(definition, handler)

    def get(self, tool_id: str) -> RegisteredTool:
        try:
            return self._tools[tool_id]
        except KeyError as error:
            raise MissingToolError(f"tool is not registered: {tool_id}") from error

    def list(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[key].definition for key in sorted(self._tools))
