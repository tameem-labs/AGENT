"""Contracts for the ZYRO Brain and Planner subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from zyro.core.data import validate_text


class PlanIntent(StrEnum):
    CONVERSATION = "CONVERSATION"
    RESEARCH = "RESEARCH"
    FREELANCE_DISCOVERY = "FREELANCE_DISCOVERY"
    FREELANCE_OUTREACH = "FREELANCE_OUTREACH"
    CODING_INSPECTION = "CODING_INSPECTION"
    CODING_DEVELOPMENT = "CODING_DEVELOPMENT"
    CONTENT_CREATION = "CONTENT_CREATION"
    CONTENT_PUBLISHING = "CONTENT_PUBLISHING"
    PERSONAL_TASK = "PERSONAL_TASK"
    OPERATIONS_CHECK = "OPERATIONS_CHECK"
    MULTI_STEP_WORKFLOW = "MULTI_STEP_WORKFLOW"


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    name: str
    description: str
    capability: str
    agent_id: str
    tools: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    approval_required: bool = False
    verification_plan: str = "structural_consistency"
    timeout_seconds: int = 30
    max_attempts: int = 2

    def __post_init__(self) -> None:
        for field_name in ("step_id", "name", "description", "capability", "agent_id"):
            object.__setattr__(
                self, field_name, validate_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "tools",
            tuple(validate_text(item, "tool") for item in self.tools),
        )
        object.__setattr__(
            self,
            "dependencies",
            tuple(validate_text(item, "dependency") for item in self.dependencies),
        )
        object.__setattr__(
            self,
            "permissions",
            tuple(validate_text(item, "permission") for item in self.permissions),
        )
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("step max_attempts must be between 1 and 10")
        if self.timeout_seconds < 1:
            raise ValueError("step timeout_seconds must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "capability": self.capability,
            "agent_id": self.agent_id,
            "tools": list(self.tools),
            "dependencies": list(self.dependencies),
            "permissions": list(self.permissions),
            "approval_required": self.approval_required,
            "verification_plan": self.verification_plan,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
        }


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    objective: str
    intent: PlanIntent
    assumptions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    confidence: float = 1.0
    steps: tuple[PlanStep, ...] = ()
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        for field_name in ("plan_id", "objective"):
            object.__setattr__(
                self, field_name, validate_text(getattr(self, field_name), field_name)
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        if not self.steps:
            raise ValueError("plan requires at least one step")
        self.validate_dag()

    def validate_dag(self) -> None:
        ids = {step.step_id for step in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("plan step identities must be unique")
        for step in self.steps:
            missing = set(step.dependencies) - ids
            if missing:
                raise ValueError(f"step {step.step_id} has nonexistent dependencies: {missing}")

        # Check for cycles using Kahn's algorithm
        in_degree: dict[str, int] = {s.step_id: 0 for s in self.steps}
        adjacency: dict[str, list[str]] = {s.step_id: [] for s in self.steps}
        for step in self.steps:
            for dep in step.dependencies:
                adjacency[dep].append(step.step_id)
                in_degree[step.step_id] += 1

        queue = [step_id for step_id, degree in in_degree.items() if degree == 0]
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(self.steps):
            raise ValueError("plan steps contain cyclic dependencies")

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "intent": self.intent.value,
            "assumptions": list(self.assumptions),
            "constraints": list(self.constraints),
            "confidence": self.confidence,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at.isoformat(),
        }


__all__ = ["Plan", "PlanIntent", "PlanStep"]
