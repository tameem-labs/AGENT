"""Bounded tool execution behind registry, validation, and risk gates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from zyro.core.errors import ErrorInfo
from zyro.core.logging import LogContext, get_logger
from zyro.core.risk import RiskClass
from zyro.tools.contracts import (
    ToolCall,
    ToolExecutionContext,
    ToolHandlerResult,
    ToolResult,
    ToolResultStatus,
)
from zyro.tools.errors import MissingToolError
from zyro.tools.registry import ToolRegistry


class ToolExecutor:
    """Execute one registered automatic-risk tool; never infer authorization."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, call: ToolCall) -> ToolResult:
        validation_error = call.validation_error()
        if validation_error is not None:
            return self._failure(
                call,
                ToolResultStatus.INVALID_INPUT,
                ErrorInfo(
                    code="invalid_tool_call",
                    message=validation_error,
                    error_type="InvalidToolCall",
                ),
            )
        try:
            registered = self._registry.get(call.tool_id)
        except MissingToolError:
            return self._failure(
                call,
                ToolResultStatus.NOT_FOUND,
                ErrorInfo(
                    code="tool_not_found",
                    message=f"Tool is not registered: {call.tool_id}",
                    error_type="MissingTool",
                ),
            )

        definition = registered.definition
        if not definition.enabled:
            return self._failure(
                call,
                ToolResultStatus.DISABLED,
                ErrorInfo(
                    code="tool_disabled",
                    message=f"Tool is disabled: {call.tool_id}",
                    error_type="DisabledTool",
                ),
            )
        if definition.risk_class is not RiskClass.AUTOMATIC:
            return self._failure(
                call,
                ToolResultStatus.AUTHORIZATION_REQUIRED,
                ErrorInfo(
                    code="tool_authorization_required",
                    message=(
                        "Tool execution requires a future permission/approval decision; "
                        "risk metadata is not authorization."
                    ),
                    error_type="AuthorizationRequired",
                ),
            )
        schema_error = validate_object_schema(call.arguments, definition.input_schema)
        if schema_error is not None:
            return self._failure(
                call,
                ToolResultStatus.INVALID_INPUT,
                ErrorInfo(
                    code="invalid_tool_input",
                    message=schema_error,
                    error_type="ToolInputValidation",
                ),
            )

        logger = get_logger(
            "tool_executor",
            LogContext(
                request_id=call.request_id,
                task_id=call.task_id,
                agent_id=call.agent_id,
                instance_id=call.instance_id,
                correlation_id=call.correlation_id,
            ),
        )
        context = ToolExecutionContext(
            tool_id=call.tool_id,
            request_id=call.request_id,
            task_id=call.task_id,
            agent_id=call.agent_id,
            instance_id=call.instance_id,
            correlation_id=call.correlation_id,
        )
        logger.info("bounded tool execution started")
        try:
            handler_result = registered.handler.execute(context, call.arguments)
        except TimeoutError:
            logger.warning("tool execution timed out")
            return self._failure(
                call,
                ToolResultStatus.TIMEOUT,
                ErrorInfo(
                    code="tool_timeout",
                    message="The tool handler timed out.",
                    error_type="TimeoutError",
                    retryable=True,
                ),
            )
        except Exception as error:
            logger.error("tool handler raised an exception")
            return self._failure(
                call,
                ToolResultStatus.HANDLER_EXCEPTION,
                ErrorInfo(
                    code="tool_handler_exception",
                    message=f"The tool handler raised {type(error).__name__}.",
                    error_type=type(error).__name__,
                ),
            )

        if not isinstance(handler_result, ToolHandlerResult):
            return self._malformed(call, "Tool handler returned an invalid result contract.")
        if not handler_result.succeeded:
            assert handler_result.error is not None
            logger.warning("tool execution failed")
            return self._failure(
                call,
                ToolResultStatus.EXECUTION_FAILURE,
                handler_result.error,
            )
        assert handler_result.output is not None
        output_error = validate_object_schema(handler_result.output, definition.output_schema)
        if output_error is not None:
            return self._malformed(call, f"Tool output failed its contract: {output_error}")
        logger.info("bounded tool execution succeeded")
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            tool_id=call.tool_id,
            request_id=call.request_id,
            task_id=call.task_id,
            agent_id=call.agent_id,
            instance_id=call.instance_id,
            correlation_id=call.correlation_id,
            output=handler_result.output,
        )

    def _malformed(self, call: ToolCall, message: str) -> ToolResult:
        return self._failure(
            call,
            ToolResultStatus.MALFORMED_RESULT,
            ErrorInfo(
                code="malformed_tool_result",
                message=message,
                error_type="MalformedToolResult",
            ),
        )

    @staticmethod
    def _failure(
        call: ToolCall,
        status: ToolResultStatus,
        error: ErrorInfo,
    ) -> ToolResult:
        return ToolResult(
            status=status,
            tool_id=call.tool_id,
            request_id=call.request_id,
            task_id=call.task_id,
            agent_id=call.agent_id,
            instance_id=call.instance_id,
            correlation_id=call.correlation_id,
            error=error,
        )


def validate_object_schema(
    payload: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> str | None:
    """Validate the small object-schema subset supported by Phase 3 tools."""
    if schema.get("type", "object") != "object":
        return "schema root type must be object"
    properties = schema.get("properties", {})
    required = schema.get("required", ())
    if not isinstance(properties, Mapping) or not isinstance(required, (list, tuple)):
        return "schema properties or required declaration is malformed"
    for key in required:
        if not isinstance(key, str):
            return "schema required values must be strings"
        if key not in payload:
            return f"missing required field: {key}"
    if schema.get("additionalProperties", True) is False:
        unexpected = sorted(set(payload) - set(properties))
        if unexpected:
            return f"unexpected field(s): {', '.join(unexpected)}"
    for key, value in payload.items():
        property_schema = properties.get(key)
        if property_schema is None:
            continue
        if not isinstance(property_schema, Mapping):
            return f"schema for field {key} is malformed"
        expected_type = property_schema.get("type")
        if expected_type is not None and not _matches_json_type(value, expected_type):
            return f"field {key} must be of type {expected_type}"
    return None


def _matches_json_type(value: Any, expected_type: object) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, (list, tuple))
    if expected_type == "null":
        return value is None
    return False
