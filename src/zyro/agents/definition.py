"""Immutable agent definition metadata, separate from runtime instances."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from zyro.core.errors import InvalidAgentError


class RiskClass(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    POLICY_CONTROLLED = "POLICY_CONTROLLED"
    STRICT_AUTHORIZATION = "STRICT_AUTHORIZATION"


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """What an agent is and the bounded contract it advertises."""

    agent_id: str
    name: str
    version: str
    role: str
    domain: str
    responsibilities: tuple[str, ...]
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...] = ()
    risk_class: RiskClass = RiskClass.AUTOMATIC
    input_requirements: tuple[str, ...] = ()
    output_contract: Mapping[str, Any] = field(default_factory=dict)
    context_requirements: tuple[str, ...] = ()
    communication_rules: tuple[str, ...] = ()
    model_requirements: tuple[str, ...] = ()
    verification_requirements: tuple[str, ...] = ()
    resource_limits: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("agent_id", "name", "version", "role", "domain"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidAgentError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        if not self.responsibilities:
            raise InvalidAgentError("an agent must have at least one responsibility")
        if not self.capabilities:
            raise InvalidAgentError("an agent must have at least one capability")
        if any(value < 0 for value in self.resource_limits.values()):
            raise InvalidAgentError("resource limits cannot be negative")
        object.__setattr__(self, "output_contract", MappingProxyType(dict(self.output_contract)))
        object.__setattr__(self, "resource_limits", MappingProxyType(dict(self.resource_limits)))
