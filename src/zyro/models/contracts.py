"""Provider-independent model metadata, requests, routing, and result contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from zyro.core.errors import ErrorInfo
from zyro.models.errors import InvalidModelDefinitionError, InvalidModelRequirementsError


def _clean_identifier(value: str, field_name: str, error_type: type[ValueError]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{field_name} must be a non-empty string")
    return value.strip()


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _reject_sensitive_metadata(value: Mapping[str, Any]) -> None:
    sensitive_names = {"secret", "password", "credential", "api_key", "access_token"}
    for key, item in value.items():
        normalized = key.lower().replace("-", "_")
        if normalized in sensitive_names:
            raise InvalidModelDefinitionError(
                "model metadata cannot contain credential or secret fields"
            )
        if isinstance(item, Mapping):
            _reject_sensitive_metadata(item)


class ModelComplexity(IntEnum):
    SIMPLE = 1
    MEDIUM = 2
    COMPLEX = 3


class ModelDeployment(StrEnum):
    CLOUD = "CLOUD"
    PRIVATE = "PRIVATE"
    LOCAL = "LOCAL"


class PrivacyRequirement(StrEnum):
    ANY = "ANY"
    PRIVATE_OR_LOCAL = "PRIVATE_OR_LOCAL"
    LOCAL_ONLY = "LOCAL_ONLY"


class RoutingPreference(StrEnum):
    BALANCED = "BALANCED"
    LOWEST = "LOWEST"


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    """Non-secret metadata describing one provider-hosted model."""

    model_id: str
    provider_id: str
    name: str
    capabilities: frozenset[str]
    modalities: frozenset[str]
    max_context_tokens: int
    max_complexity: ModelComplexity = ModelComplexity.MEDIUM
    supports_tool_calling: bool = False
    supports_structured_output: bool = False
    deployment: ModelDeployment = ModelDeployment.CLOUD
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    typical_latency_ms: int | None = None
    enabled: bool = True
    configuration_reference: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        error_type = InvalidModelDefinitionError
        for field_name in ("model_id", "provider_id", "name"):
            object.__setattr__(
                self,
                field_name,
                _clean_identifier(getattr(self, field_name), field_name, error_type),
            )
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "modalities", frozenset(self.modalities))
        if not self.capabilities:
            raise error_type("capabilities must not be empty")
        if not self.modalities:
            raise error_type("modalities must not be empty")
        if any(not value.strip() for value in self.capabilities | self.modalities):
            raise error_type("capabilities and modalities must not contain empty values")
        if self.max_context_tokens < 1:
            raise error_type("max_context_tokens must be at least 1")
        for field_name in ("input_cost_per_million", "output_cost_per_million"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise error_type(f"{field_name} cannot be negative")
        if self.typical_latency_ms is not None and self.typical_latency_ms < 0:
            raise error_type("typical_latency_ms cannot be negative")
        if self.configuration_reference is not None:
            object.__setattr__(
                self,
                "configuration_reference",
                _clean_identifier(
                    self.configuration_reference,
                    "configuration_reference",
                    error_type,
                ),
            )
        _reject_sensitive_metadata(self.metadata)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def estimated_total_cost(self) -> float | None:
        if self.input_cost_per_million is None or self.output_cost_per_million is None:
            return None
        return self.input_cost_per_million + self.output_cost_per_million


@dataclass(frozen=True, slots=True)
class ModelRequirements:
    """What an agent needs, deliberately excluding provider and model identities."""

    task_type: str
    required_capabilities: frozenset[str] = frozenset()
    required_modalities: frozenset[str] = frozenset({"text"})
    minimum_context_tokens: int = 1
    complexity: ModelComplexity = ModelComplexity.MEDIUM
    tool_calling_required: bool = False
    structured_output_required: bool = False
    maximum_cost_per_million: float | None = None
    maximum_latency_ms: int | None = None
    cost_preference: RoutingPreference = RoutingPreference.BALANCED
    latency_preference: RoutingPreference = RoutingPreference.BALANCED
    privacy: PrivacyRequirement = PrivacyRequirement.ANY
    availability_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_capabilities",
            frozenset(self.required_capabilities),
        )
        object.__setattr__(self, "required_modalities", frozenset(self.required_modalities))
        object.__setattr__(
            self,
            "task_type",
            _clean_identifier(
                self.task_type,
                "task_type",
                InvalidModelRequirementsError,
            ),
        )
        if any(
            not value.strip() for value in self.required_capabilities | self.required_modalities
        ):
            raise InvalidModelRequirementsError(
                "required capabilities and modalities cannot contain empty values"
            )
        if self.minimum_context_tokens < 1:
            raise InvalidModelRequirementsError("minimum_context_tokens must be at least 1")
        if self.maximum_cost_per_million is not None and self.maximum_cost_per_million < 0:
            raise InvalidModelRequirementsError("maximum cost cannot be negative")
        if self.maximum_latency_ms is not None and self.maximum_latency_ms < 0:
            raise InvalidModelRequirementsError("maximum latency cannot be negative")


@dataclass(frozen=True, slots=True)
class RequestedToolCall:
    """A model suggestion only; it is not authorization or execution."""

    tool_id: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tool_id",
            _clean_identifier(self.tool_id, "tool_id", ValueError),
        )
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class ModelRequest:
    prompt: str
    requirements: ModelRequirements
    request_id: str
    task_id: str
    agent_id: str
    instance_id: str
    correlation_id: str
    system_instruction: str | None = None
    structured_output_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.structured_output_schema is not None:
            object.__setattr__(
                self,
                "structured_output_schema",
                _freeze_mapping(self.structured_output_schema),
            )

    def validation_error(self) -> str | None:
        for field_name in (
            "prompt",
            "request_id",
            "task_id",
            "agent_id",
            "instance_id",
            "correlation_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                return f"{field_name} must be a non-empty string"
        if self.system_instruction is not None and not self.system_instruction.strip():
            return "system_instruction must be non-empty when supplied"
        return None


class ModelResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    NO_ELIGIBLE_MODEL = "NO_ELIGIBLE_MODEL"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    TIMEOUT = "TIMEOUT"
    INVALID_REQUEST = "INVALID_REQUEST"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    INVOCATION_EXCEPTION = "INVOCATION_EXCEPTION"


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_units: int = 0
    output_units: int = 0

    def __post_init__(self) -> None:
        if self.input_units < 0 or self.output_units < 0:
            raise ValueError("model usage cannot be negative")


@dataclass(frozen=True, slots=True)
class ModelResult:
    """Structured model outcome with no implied semantic verification."""

    status: ModelResultStatus
    request_id: str
    task_id: str
    agent_id: str
    instance_id: str
    correlation_id: str
    provider_id: str | None = None
    model_id: str | None = None
    content: str | None = None
    structured_output: Mapping[str, Any] | None = None
    requested_tool_calls: tuple[RequestedToolCall, ...] = ()
    usage: ModelUsage = field(default_factory=ModelUsage)
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_tool_calls", tuple(self.requested_tool_calls))
        if self.status is ModelResultStatus.SUCCESS:
            if self.error is not None:
                raise ValueError("successful model result cannot contain an error")
            if self.provider_id is None or self.model_id is None:
                raise ValueError("successful model result requires provider and model identities")
            if self.content is None and self.structured_output is None:
                raise ValueError("successful model result requires content or structured output")
        elif self.error is None:
            raise ValueError("failed model result requires a structured error")
        if self.structured_output is not None:
            object.__setattr__(
                self,
                "structured_output",
                _freeze_mapping(self.structured_output),
            )

    @property
    def succeeded(self) -> bool:
        return self.status is ModelResultStatus.SUCCESS


class ModelInvoker(Protocol):
    """Narrow agent-facing boundary implemented by the ModelRouter."""

    def invoke(self, request: ModelRequest) -> ModelResult:
        """Route and invoke without accepting a provider or model identity."""
        ...


class RoutingStatus(StrEnum):
    SELECTED = "SELECTED"
    NO_ELIGIBLE_MODEL = "NO_ELIGIBLE_MODEL"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    status: RoutingStatus
    model: ModelDefinition | None = None
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if self.status is RoutingStatus.SELECTED:
            if self.model is None or self.error is not None:
                raise ValueError("selected routing decision requires only a model")
        elif self.model is not None or self.error is None:
            raise ValueError("failed routing decision requires only an error")
