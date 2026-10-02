"""Provider-independent bounded agent execution contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from zyro.context.contracts import (
    AssembledContext,
    ContextBudget,
    ContextRequest,
    StateReference,
)
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope
from zyro.models.contracts import (
    ModelInvoker,
    ModelRequest,
    ModelRequirements,
    ModelResult,
    ModelResultStatus,
)
from zyro.tools.contracts import (
    ToolCall,
    ToolInvoker,
    ToolResult,
    ToolResultStatus,
)


class ContextProvider(Protocol):
    def assemble(self, request: ContextRequest) -> AssembledContext: ...


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    task_id: str
    request_id: str
    correlation_id: str
    agent_id: str
    instance_id: str
    goal: str
    attempt: int
    model_invoker: ModelInvoker | None = None
    tool_invoker: ToolInvoker | None = None
    default_model_requirements: ModelRequirements | None = None
    requester_id: str | None = None
    pending_approval_id: str | None = None
    context_provider: ContextProvider | None = None

    def request_context(
        self,
        scope: ResourceScope,
        query: str,
        *,
        task_data: Mapping[str, Any] | None = None,
        state_references: tuple[StateReference, ...] = (),
        budget: ContextBudget | None = None,
    ) -> AssembledContext:
        """Request a bounded view; the agent receives no direct store access."""
        if self.context_provider is None or self.requester_id is None:
            return AssembledContext(
                self.task_id,
                (),
                0,
                False,
                ErrorInfo(
                    "context_provider_unavailable",
                    "No Context Assembler is attached to this runtime.",
                    "ContextUnavailable",
                ),
            )
        return self.context_provider.assemble(
            ContextRequest(
                requester_id=self.requester_id,
                task_id=self.task_id,
                scope=scope,
                query=query,
                current_instruction=self.goal,
                task_data={} if task_data is None else task_data,
                state_references=state_references,
                budget=budget or ContextBudget(),
            )
        )

    def invoke_model(
        self,
        prompt: str,
        requirements: ModelRequirements | None = None,
        *,
        system_instruction: str | None = None,
        structured_output_schema: Mapping[str, Any] | None = None,
    ) -> ModelResult:
        """Request capabilities through the router without naming a provider/model."""
        selected_requirements = requirements or self.default_model_requirements
        if selected_requirements is None:
            return self._model_boundary_failure(
                ModelResultStatus.INVALID_REQUEST,
                "model_requirements_missing",
                "No provider-independent model requirements were supplied.",
            )
        model_request = ModelRequest(
            prompt=prompt,
            requirements=selected_requirements,
            request_id=self.request_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            instance_id=self.instance_id,
            correlation_id=self.correlation_id,
            system_instruction=system_instruction,
            structured_output_schema=structured_output_schema,
        )
        validation_error = model_request.validation_error()
        if validation_error is not None:
            return self._model_boundary_failure(
                ModelResultStatus.INVALID_REQUEST,
                "invalid_model_request",
                validation_error,
            )
        if self.model_invoker is None:
            return self._model_boundary_failure(
                ModelResultStatus.PROVIDER_UNAVAILABLE,
                "model_router_unavailable",
                "No model router is attached to this agent runtime.",
            )
        return self.model_invoker.invoke(model_request)

    def execute_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        capability: str | None = None,
        target: str | None = None,
        purpose: str | None = None,
        expected_effect: str | None = None,
        approval_id: str | None = None,
        conditions: Mapping[str, Any] | None = None,
        approval_context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Request one authorized bounded call; model suggestions grant no authority."""
        call = ToolCall(
            tool_id=tool_id,
            arguments=arguments,
            request_id=self.request_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            instance_id=self.instance_id,
            correlation_id=self.correlation_id,
            requester_id=self.requester_id,
            capability=capability,
            target=target,
            purpose=purpose,
            expected_effect=expected_effect,
            approval_id=approval_id or self.pending_approval_id,
            conditions={} if conditions is None else conditions,
            approval_context={} if approval_context is None else approval_context,
        )
        validation_error = call.validation_error()
        if validation_error is not None:
            return ToolResult(
                status=ToolResultStatus.INVALID_INPUT,
                tool_id=tool_id,
                request_id=self.request_id,
                task_id=self.task_id,
                agent_id=self.agent_id,
                instance_id=self.instance_id,
                correlation_id=self.correlation_id,
                error=ErrorInfo(
                    code="invalid_tool_call",
                    message=validation_error,
                    error_type="InvalidToolCall",
                ),
            )
        if self.tool_invoker is None:
            return ToolResult(
                status=ToolResultStatus.EXECUTION_FAILURE,
                tool_id=tool_id,
                request_id=self.request_id,
                task_id=self.task_id,
                agent_id=self.agent_id,
                instance_id=self.instance_id,
                correlation_id=self.correlation_id,
                error=ErrorInfo(
                    code="tool_executor_unavailable",
                    message="No tool executor is attached to this agent runtime.",
                    error_type="ToolExecutorUnavailable",
                ),
            )
        return self.tool_invoker.execute(call)

    def _model_boundary_failure(
        self,
        status: ModelResultStatus,
        code: str,
        message: str,
    ) -> ModelResult:
        return ModelResult(
            status=status,
            request_id=self.request_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            instance_id=self.instance_id,
            correlation_id=self.correlation_id,
            error=ErrorInfo(code=code, message=message, error_type="ModelBoundaryFailure"),
        )


@dataclass(frozen=True, slots=True)
class AgentExecution:
    """Structured output of bounded agent logic and its model/tool evidence."""

    succeeded: bool
    value: Any | None = None
    error: ErrorInfo | None = None
    model_results: tuple[ModelResult, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()

    def __post_init__(self) -> None:
        if self.succeeded and self.error is not None:
            raise ValueError("a successful execution cannot contain an error")
        if not self.succeeded and self.error is None:
            raise ValueError("a failed execution must contain an error")
        if self.succeeded and any(not result.succeeded for result in self.model_results):
            raise ValueError("successful agent execution cannot contain failed model results")
        if self.succeeded and any(not result.succeeded for result in self.tool_results):
            raise ValueError("successful agent execution cannot contain failed tool results")

    @property
    def outcome_unknown(self) -> bool:
        return any(result.outcome_unknown for result in self.tool_results)

    @property
    def waiting_for_approval(self) -> bool:
        return any(
            result.status is ToolResultStatus.APPROVAL_PENDING for result in self.tool_results
        )

    @property
    def pending_approval_id(self) -> str | None:
        for result in self.tool_results:
            if result.status is ToolResultStatus.APPROVAL_PENDING:
                return result.approval_id
        return None

    @classmethod
    def success(
        cls,
        value: Any,
        *,
        model_results: tuple[ModelResult, ...] = (),
        tool_results: tuple[ToolResult, ...] = (),
    ) -> AgentExecution:
        return cls(
            succeeded=True,
            value=value,
            model_results=model_results,
            tool_results=tool_results,
        )

    @classmethod
    def failure(
        cls,
        error: ErrorInfo,
        *,
        model_results: tuple[ModelResult, ...] = (),
        tool_results: tuple[ToolResult, ...] = (),
    ) -> AgentExecution:
        return cls(
            succeeded=False,
            error=error,
            model_results=model_results,
            tool_results=tool_results,
        )


class AgentHandler(Protocol):
    """Bounded logic attached to an agent definition, not a model or tool."""

    def execute(self, context: ExecutionContext) -> AgentExecution:
        """Execute one attempt and return a structured result."""
        ...
