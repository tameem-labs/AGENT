"""Bounded tool definition, call, handler, and result contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from zyro.core.errors import ErrorInfo
from zyro.core.risk import RiskClass
from zyro.tools.errors import InvalidToolDefinitionError


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


def _clean(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidToolDefinitionError(f"{field_name} must be a non-empty string")
    return value.strip()


def _reject_sensitive_metadata(value: Mapping[str, Any]) -> None:
    sensitive_names = {"secret", "password", "credential", "api_key", "access_token"}
    for key, item in value.items():
        normalized = key.lower().replace("-", "_")
        if normalized in sensitive_names:
            raise InvalidToolDefinitionError(
                "tool metadata cannot contain credential or secret fields"
            )
        if isinstance(item, Mapping):
            _reject_sensitive_metadata(item)


def _validate_schema(schema: Mapping[str, Any], field_name: str) -> None:
    if schema.get("type", "object") != "object":
        raise InvalidToolDefinitionError(f"{field_name} root type must be object")
    properties = schema.get("properties", {})
    required = schema.get("required", ())
    if not isinstance(properties, Mapping) or not isinstance(required, (list, tuple)):
        raise InvalidToolDefinitionError(f"{field_name} has malformed properties or required")
    if any(not isinstance(key, str) for key in required):
        raise InvalidToolDefinitionError(f"{field_name} required values must be strings")
    unknown_required = set(required) - set(properties)
    if unknown_required:
        raise InvalidToolDefinitionError(
            f"{field_name} requires undefined properties: {sorted(unknown_required)}"
        )
    supported_types = {"string", "integer", "number", "boolean", "object", "array", "null"}
    for key, property_schema in properties.items():
        if not isinstance(key, str) or not isinstance(property_schema, Mapping):
            raise InvalidToolDefinitionError(f"{field_name} property schemas are malformed")
        value_type = property_schema.get("type")
        if value_type is not None and value_type not in supported_types:
            raise InvalidToolDefinitionError(
                f"{field_name} property {key} has unsupported type: {value_type}"
            )


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Non-secret metadata for one bounded capability; risk is not authorization."""

    tool_id: str
    name: str
    version: str
    description: str
    capabilities: frozenset[str]
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    handler_id: str
    risk_class: RiskClass = RiskClass.AUTOMATIC
    enabled: bool = True
    timeout_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("tool_id", "name", "version", "description", "handler_id"):
            object.__setattr__(self, field_name, _clean(getattr(self, field_name), field_name))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        if not self.capabilities or any(not value.strip() for value in self.capabilities):
            raise InvalidToolDefinitionError("capabilities must contain non-empty values")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise InvalidToolDefinitionError("timeout_seconds must be positive")
        _validate_schema(self.input_schema, "input_schema")
        _validate_schema(self.output_schema, "output_schema")
        _reject_sensitive_metadata(self.metadata)
        for field_name in ("input_schema", "output_schema", "metadata"):
            object.__setattr__(self, field_name, _freeze_mapping(getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_id: str
    arguments: Mapping[str, Any]
    request_id: str
    task_id: str
    agent_id: str
    instance_id: str
    correlation_id: str
    requester_id: str | None = None
    capability: str | None = None
    target: str | None = None
    purpose: str | None = None
    expected_effect: str | None = None
    approval_id: str | None = None
    conditions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))
        object.__setattr__(self, "conditions", _freeze_mapping(self.conditions))

    def validation_error(self) -> str | None:
        for field_name in (
            "tool_id",
            "request_id",
            "task_id",
            "agent_id",
            "instance_id",
            "correlation_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                return f"{field_name} must be a non-empty string"
        for field_name in (
            "requester_id",
            "capability",
            "target",
            "purpose",
            "expected_effect",
            "approval_id",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                return f"{field_name} must be non-empty when supplied"
        try:
            _reject_sensitive_metadata(self.conditions)
        except InvalidToolDefinitionError:
            return "tool call conditions cannot contain secret fields"
        return None


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    tool_id: str
    request_id: str
    task_id: str
    agent_id: str
    instance_id: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ToolHandlerResult:
    succeeded: bool
    output: Mapping[str, Any] | None = None
    error: ErrorInfo | None = None
    unknown: bool = False

    def __post_init__(self) -> None:
        if self.unknown:
            if self.succeeded or self.error is None:
                raise ValueError("unknown tool outcome requires an error and cannot be success")
            if self.output is not None:
                object.__setattr__(self, "output", _freeze_mapping(self.output))
        elif self.succeeded:
            if self.error is not None or self.output is None:
                raise ValueError("successful tool handler result requires only output")
            object.__setattr__(self, "output", _freeze_mapping(self.output))
        elif self.error is None or self.output is not None:
            raise ValueError("failed tool handler result requires only an error")

    @classmethod
    def success(cls, output: Mapping[str, Any]) -> ToolHandlerResult:
        return cls(succeeded=True, output=output)

    @classmethod
    def failure(cls, error: ErrorInfo) -> ToolHandlerResult:
        return cls(succeeded=False, error=error)

    @classmethod
    def unknown_outcome(
        cls,
        error: ErrorInfo,
        output: Mapping[str, Any] | None = None,
    ) -> ToolHandlerResult:
        return cls(succeeded=False, output=output, error=error, unknown=True)


class ToolHandler(Protocol):
    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        """Execute one bounded capability with already validated input."""
        ...


class ToolAuthorizationStatus(StrEnum):
    ALLOW = "ALLOW"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INVALID = "APPROVAL_INVALID"


@dataclass(frozen=True, slots=True)
class ToolAuthorizationDecision:
    status: ToolAuthorizationStatus
    permission_decision_id: str
    policy_version: str
    error: ErrorInfo | None = None
    permission_id: str | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if self.status is ToolAuthorizationStatus.ALLOW and self.error is not None:
            raise ValueError("allowed tool authorization cannot contain an error")
        if self.status is not ToolAuthorizationStatus.ALLOW and self.error is None:
            raise ValueError("blocked tool authorization requires an error")

    @property
    def allowed(self) -> bool:
        return self.status is ToolAuthorizationStatus.ALLOW


class ToolAuthorizer(Protocol):
    """Separate authority boundary called by ToolExecutor before side effects."""

    def authorize(
        self,
        call: ToolCall,
        definition: ToolDefinition,
    ) -> ToolAuthorizationDecision: ...


class ToolInvoker(Protocol):
    """Narrow agent-facing boundary implemented by the ToolExecutor."""

    def execute(self, call: ToolCall) -> ToolResult:
        """Authorize, resolve, and execute one bounded call behind the registry boundary."""
        ...


class ToolResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    UNKNOWN = "UNKNOWN"
    NOT_FOUND = "NOT_FOUND"
    DISABLED = "DISABLED"
    INVALID_INPUT = "INVALID_INPUT"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    TIMEOUT = "TIMEOUT"
    HANDLER_EXCEPTION = "HANDLER_EXCEPTION"
    MALFORMED_RESULT = "MALFORMED_RESULT"


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: ToolResultStatus
    tool_id: str
    request_id: str
    task_id: str
    agent_id: str
    instance_id: str
    correlation_id: str
    output: Mapping[str, Any] | None = None
    error: ErrorInfo | None = None
    permission_decision_id: str | None = None
    permission_id: str | None = None
    approval_id: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        if self.status is ToolResultStatus.SUCCESS:
            if self.error is not None or self.output is None:
                raise ValueError("successful tool result requires only output")
            object.__setattr__(self, "output", _freeze_mapping(self.output))
        elif self.status is ToolResultStatus.UNKNOWN:
            if self.error is None:
                raise ValueError("unknown tool result requires a structured error")
            if self.output is not None:
                object.__setattr__(self, "output", _freeze_mapping(self.output))
        elif self.error is None or self.output is not None:
            raise ValueError("failed tool result requires only an error")

    @property
    def succeeded(self) -> bool:
        return self.status is ToolResultStatus.SUCCESS

    @property
    def outcome_unknown(self) -> bool:
        return self.status is ToolResultStatus.UNKNOWN
