"""Bounded tool execution with mandatory external authorization before side effects."""

from __future__ import annotations

from collections.abc import Mapping
from queue import Empty, Queue
from threading import Thread
from typing import Any

from zyro.core.errors import ErrorInfo
from zyro.core.logging import LogContext, get_logger
from zyro.tools.contracts import (
    ToolAuthorizationDecision,
    ToolAuthorizationStatus,
    ToolAuthorizer,
    ToolCall,
    ToolExecutionContext,
    ToolHandlerResult,
    ToolResult,
    ToolResultStatus,
)
from zyro.tools.errors import MissingToolError
from zyro.tools.registry import RegisteredTool, ToolRegistry


class ToolExecutor:
    """Execute one bounded tool only after a separate authorizer explicitly allows it."""

    def __init__(self, registry: ToolRegistry, authorizer: ToolAuthorizer) -> None:
        self._registry = registry
        self._authorizer = authorizer

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

        try:
            authorization = self._authorizer.authorize(call, definition)
        except Exception as error:
            return self._failure(
                call,
                ToolResultStatus.AUTHORIZATION_REQUIRED,
                ErrorInfo(
                    code="authorization_boundary_error",
                    message=(
                        "Authorization could not be established; execution was blocked "
                        f"after {type(error).__name__}."
                    ),
                    error_type="AuthorizationBoundaryError",
                ),
            )
        if not authorization.allowed:
            assert authorization.error is not None
            return self._failure(
                call,
                self._authorization_result_status(authorization.status),
                authorization.error,
                authorization,
            )
        if authorization.dispatch_grant_id is not None:
            grants = getattr(self._authorizer, "dispatch_grants", None)
            if grants is None or authorization.action_fingerprint is None:
                return self._failure(
                    call,
                    ToolResultStatus.AUTHORIZATION_REQUIRED,
                    ErrorInfo(
                        "dispatch_grant_unavailable",
                        "Bounded dispatch authority could not be claimed.",
                        "AuthorizationBoundaryError",
                    ),
                    authorization,
                )
            try:
                grants.claim(
                    authorization.dispatch_grant_id,
                    authorization.action_fingerprint,
                )
            except Exception:
                return self._failure(
                    call,
                    ToolResultStatus.AUTHORIZATION_REQUIRED,
                    ErrorInfo(
                        "dispatch_grant_invalid",
                        "Authority changed before dispatch; execution was blocked.",
                        "AuthorizationBoundaryError",
                    ),
                    authorization,
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
        logger.info("authorized bounded tool execution started")
        try:
            handler_result = self._execute_handler(
                registered,
                context,
                call.arguments,
                definition.timeout_seconds,
            )
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
                authorization,
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
                authorization,
            )

        if not isinstance(handler_result, ToolHandlerResult):
            return self._malformed(
                call,
                "Tool handler returned an invalid result contract.",
                authorization,
            )
        if handler_result.unknown:
            assert handler_result.error is not None
            logger.warning("tool execution outcome is unknown")
            return ToolResult(
                status=ToolResultStatus.UNKNOWN,
                tool_id=call.tool_id,
                request_id=call.request_id,
                task_id=call.task_id,
                agent_id=call.agent_id,
                instance_id=call.instance_id,
                correlation_id=call.correlation_id,
                workflow_id=call.workflow_id,
                output=handler_result.output,
                error=handler_result.error,
                permission_decision_id=authorization.permission_decision_id,
                permission_id=authorization.permission_id,
                approval_id=authorization.approval_id,
                policy_version=authorization.policy_version,
            )
        if not handler_result.succeeded:
            assert handler_result.error is not None
            logger.warning("tool execution failed")
            return self._failure(
                call,
                ToolResultStatus.EXECUTION_FAILURE,
                handler_result.error,
                authorization,
            )
        assert handler_result.output is not None
        output_error = validate_object_schema(handler_result.output, definition.output_schema)
        if output_error is not None:
            return self._malformed(
                call,
                f"Tool output failed its contract: {output_error}",
                authorization,
            )
        logger.info("authorized bounded tool execution succeeded")
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            tool_id=call.tool_id,
            request_id=call.request_id,
            task_id=call.task_id,
            agent_id=call.agent_id,
            instance_id=call.instance_id,
            correlation_id=call.correlation_id,
            workflow_id=call.workflow_id,
            output=handler_result.output,
            permission_decision_id=authorization.permission_decision_id,
            permission_id=authorization.permission_id,
            approval_id=authorization.approval_id,
            policy_version=authorization.policy_version,
        )

    @staticmethod
    def _execute_handler(
        registered: RegisteredTool,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
        timeout_seconds: float | None,
    ) -> ToolHandlerResult:
        def invoke() -> ToolHandlerResult:
            # Canonical call shape: registered.handler.execute(context, call.arguments)
            return registered.handler.execute(context, arguments)

        if timeout_seconds is None:
            return invoke()
        outcomes: Queue[ToolHandlerResult | BaseException] = Queue(maxsize=1)

        def capture() -> None:
            try:
                outcomes.put(invoke())
            except BaseException as error:
                outcomes.put(error)

        thread = Thread(target=capture, daemon=True, name=f"zyro-tool-{context.tool_id}")
        thread.start()
        try:
            outcome = outcomes.get(timeout=timeout_seconds)
        except Empty:
            return ToolHandlerResult.unknown_outcome(
                ErrorInfo(
                    "tool_timeout_outcome_unknown",
                    "Configured timeout elapsed; the blocking handler could not be "
                    "forcibly stopped.",
                    "ExternalSideEffectUncertainty",
                )
            )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def _malformed(
        self,
        call: ToolCall,
        message: str,
        authorization: ToolAuthorizationDecision,
    ) -> ToolResult:
        return self._failure(
            call,
            ToolResultStatus.MALFORMED_RESULT,
            ErrorInfo(
                code="malformed_tool_result",
                message=message,
                error_type="MalformedToolResult",
            ),
            authorization,
        )

    @staticmethod
    def _failure(
        call: ToolCall,
        status: ToolResultStatus,
        error: ErrorInfo,
        authorization: ToolAuthorizationDecision | None = None,
    ) -> ToolResult:
        return ToolResult(
            status=status,
            tool_id=call.tool_id,
            request_id=call.request_id,
            task_id=call.task_id,
            agent_id=call.agent_id,
            instance_id=call.instance_id,
            correlation_id=call.correlation_id,
            workflow_id=call.workflow_id,
            error=error,
            permission_decision_id=(
                None if authorization is None else authorization.permission_decision_id
            ),
            permission_id=None if authorization is None else authorization.permission_id,
            approval_id=None if authorization is None else authorization.approval_id,
            policy_version=None if authorization is None else authorization.policy_version,
        )

    @staticmethod
    def _authorization_result_status(status: ToolAuthorizationStatus) -> ToolResultStatus:
        return {
            ToolAuthorizationStatus.PERMISSION_DENIED: ToolResultStatus.PERMISSION_DENIED,
            ToolAuthorizationStatus.APPROVAL_PENDING: ToolResultStatus.APPROVAL_PENDING,
            ToolAuthorizationStatus.APPROVAL_DENIED: ToolResultStatus.APPROVAL_DENIED,
            ToolAuthorizationStatus.APPROVAL_EXPIRED: ToolResultStatus.APPROVAL_EXPIRED,
            ToolAuthorizationStatus.APPROVAL_INVALID: ToolResultStatus.APPROVAL_INVALID,
            ToolAuthorizationStatus.ALLOW: ToolResultStatus.AUTHORIZATION_REQUIRED,
        }[status]


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
