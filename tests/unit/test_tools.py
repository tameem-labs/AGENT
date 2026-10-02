from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from tests.fakes import EchoToolHandler
from zyro.core.errors import ErrorInfo
from zyro.core.risk import RiskClass
from zyro.tools.contracts import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolHandlerResult,
    ToolResultStatus,
)
from zyro.tools.errors import DuplicateToolError, InvalidToolDefinitionError, MissingToolError
from zyro.tools.executor import ToolExecutor
from zyro.tools.registry import ToolRegistry


def definition(
    tool_id: str = "echo",
    *,
    enabled: bool = True,
    risk_class: RiskClass = RiskClass.AUTOMATIC,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        name="Echo",
        version="1.0",
        description="Return the supplied text",
        capabilities=frozenset({"echo"}),
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
            "additionalProperties": False,
        },
        handler_id="tests.echo",
        enabled=enabled,
        risk_class=risk_class,
    )


def call(tool_id: str = "echo", arguments: Mapping[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        tool_id=tool_id,
        arguments={"text": "hello"} if arguments is None else arguments,
        request_id="request-1",
        task_id="task-1",
        agent_id="agent-1",
        instance_id="instance-1",
        correlation_id="correlation-1",
    )


def executor_with(
    handler: object | None = None,
    tool: ToolDefinition | None = None,
) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool or definition(), handler or EchoToolHandler())  # type: ignore[arg-type]
    return ToolExecutor(registry)


def test_tool_definition_validation() -> None:
    with pytest.raises(InvalidToolDefinitionError, match="tool_id"):
        definition(" ")
    with pytest.raises(InvalidToolDefinitionError, match="timeout_seconds"):
        ToolDefinition(
            tool_id="bad-timeout",
            name="Bad",
            version="1",
            description="Bad timeout",
            capabilities=frozenset({"test"}),
            input_schema={},
            output_schema={},
            handler_id="tests.bad",
            timeout_seconds=0,
        )
    with pytest.raises(InvalidToolDefinitionError, match="undefined properties"):
        ToolDefinition(
            tool_id="bad-schema",
            name="Bad",
            version="1",
            description="Bad schema",
            capabilities=frozenset({"test"}),
            input_schema={"required": ["missing"], "properties": {}},
            output_schema={},
            handler_id="tests.bad",
        )
    with pytest.raises(InvalidToolDefinitionError, match="secret fields"):
        ToolDefinition(
            tool_id="unsafe",
            name="Unsafe",
            version="1",
            description="Unsafe metadata",
            capabilities=frozenset({"test"}),
            input_schema={},
            output_schema={},
            handler_id="tests.unsafe",
            metadata={"credential": "redacted"},
        )


def test_tool_registry_registers_lists_and_rejects_duplicates() -> None:
    registry = ToolRegistry()
    handler = EchoToolHandler()
    registry.register(definition("z-tool"), handler)
    registry.register(definition("a-tool"), handler)

    assert registry.get("z-tool").handler is handler
    assert [item.tool_id for item in registry.list()] == ["a-tool", "z-tool"]
    with pytest.raises(DuplicateToolError):
        registry.register(definition("z-tool"), handler)
    with pytest.raises(MissingToolError):
        registry.get("missing")


def test_valid_tool_execution_returns_structured_result_and_correlation() -> None:
    handler = EchoToolHandler()
    result = executor_with(handler).execute(call())

    assert result.status is ToolResultStatus.SUCCESS
    assert result.output == {"text": "hello"}
    assert result.request_id == "request-1"
    assert result.instance_id == "instance-1"
    assert result.correlation_id == "correlation-1"
    assert handler.calls[0].tool_id == "echo"


def test_missing_and_disabled_tools_are_explicit_failures() -> None:
    missing = ToolExecutor(ToolRegistry()).execute(call("missing"))
    disabled = executor_with(tool=definition(enabled=False)).execute(call())

    assert missing.status is ToolResultStatus.NOT_FOUND
    assert missing.error is not None and missing.error.code == "tool_not_found"
    assert disabled.status is ToolResultStatus.DISABLED
    assert disabled.error is not None and disabled.error.code == "tool_disabled"


def test_invalid_tool_input_is_rejected_before_handler() -> None:
    handler = EchoToolHandler()
    result = executor_with(handler).execute(call(arguments={"text": 3}))

    assert result.status is ToolResultStatus.INVALID_INPUT
    assert result.error is not None
    assert result.error.code == "invalid_tool_input"
    assert handler.calls == []


class RaisingHandler:
    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        raise RuntimeError("sensitive handler details")


class TimeoutHandler:
    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        raise TimeoutError("details")


class MalformedHandler:
    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        return object()  # type: ignore[return-value]


class MalformedOutputHandler:
    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        return ToolHandlerResult.success({"text": 9})


@dataclass
class FailingHandler:
    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        return ToolHandlerResult.failure(
            ErrorInfo("bounded_failure", "Bounded execution failed.", "ToolFailure")
        )


def test_handler_exception_and_timeout_are_structured_and_sanitized() -> None:
    raised = executor_with(RaisingHandler()).execute(call())
    timeout = executor_with(TimeoutHandler()).execute(call())

    assert raised.status is ToolResultStatus.HANDLER_EXCEPTION
    assert raised.error is not None
    assert "sensitive handler details" not in raised.error.message
    assert timeout.status is ToolResultStatus.TIMEOUT
    assert timeout.error is not None and timeout.error.retryable


def test_handler_failure_is_not_converted_to_success() -> None:
    result = executor_with(FailingHandler()).execute(call())

    assert result.status is ToolResultStatus.EXECUTION_FAILURE
    assert result.error is not None
    assert result.error.code == "bounded_failure"


def test_malformed_handler_result_and_output_are_rejected() -> None:
    invalid_contract = executor_with(MalformedHandler()).execute(call())
    invalid_output = executor_with(MalformedOutputHandler()).execute(call())

    assert invalid_contract.status is ToolResultStatus.MALFORMED_RESULT
    assert invalid_output.status is ToolResultStatus.MALFORMED_RESULT


def test_risk_metadata_never_grants_tool_authorization() -> None:
    handler = EchoToolHandler()
    result = executor_with(
        handler,
        definition(risk_class=RiskClass.POLICY_CONTROLLED),
    ).execute(call())

    assert result.status is ToolResultStatus.AUTHORIZATION_REQUIRED
    assert result.error is not None
    assert result.error.code == "tool_authorization_required"
    assert handler.calls == []
