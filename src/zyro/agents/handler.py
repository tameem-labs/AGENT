"""Provider-independent bounded agent execution contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from zyro.core.errors import ErrorInfo


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    task_id: str
    request_id: str
    correlation_id: str
    agent_id: str
    instance_id: str
    goal: str
    attempt: int


@dataclass(frozen=True, slots=True)
class AgentExecution:
    """Structured output of bounded agent logic."""

    succeeded: bool
    value: Any | None = None
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if self.succeeded and self.error is not None:
            raise ValueError("a successful execution cannot contain an error")
        if not self.succeeded and self.error is None:
            raise ValueError("a failed execution must contain an error")

    @classmethod
    def success(cls, value: Any) -> AgentExecution:
        return cls(succeeded=True, value=value)

    @classmethod
    def failure(cls, error: ErrorInfo) -> AgentExecution:
        return cls(succeeded=False, error=error)


class AgentHandler(Protocol):
    """Bounded logic attached to an agent definition, not a model or tool."""

    def execute(self, context: ExecutionContext) -> AgentExecution:
        """Execute one attempt and return a structured result."""
        ...
