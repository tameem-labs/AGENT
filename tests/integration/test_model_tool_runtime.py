from __future__ import annotations

from tests.fakes import AllowTestAuthorizer, DeterministicModelProvider, EchoToolHandler
from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.errors import ErrorInfo
from zyro.core.executive import ExecutiveOutcome, UserRequest, ZyroExecutive
from zyro.core.task import Task, TaskStatus
from zyro.execution.verification import StructuralRuntimeVerifier
from zyro.models.contracts import (
    ModelComplexity,
    ModelDefinition,
    ModelRequirements,
    RequestedToolCall,
)
from zyro.models.provider import ProviderRegistry
from zyro.models.registry import ModelRegistry
from zyro.models.router import ModelRouter
from zyro.runtime.agent_runtime import AgentRuntime
from zyro.tools.contracts import ToolDefinition
from zyro.tools.executor import ToolExecutor
from zyro.tools.registry import ToolRegistry

REQUIREMENTS = ModelRequirements(
    task_type="reasoning",
    required_capabilities=frozenset({"reasoning"}),
    minimum_context_tokens=100,
    complexity=ModelComplexity.MEDIUM,
    tool_calling_required=True,
)


def agent_definition() -> AgentDefinition:
    return AgentDefinition(
        agent_id="service-agent",
        name="Service Agent",
        version="1",
        role="Exercise model and tool boundaries",
        domain="core",
        responsibilities=("invoke bounded services",),
        capabilities=("service_orchestration",),
        model_requirements=REQUIREMENTS,
    )


def model_router(provider: DeterministicModelProvider) -> ModelRouter:
    providers = ProviderRegistry()
    providers.register(provider)
    models = ModelRegistry()
    models.register(
        ModelDefinition(
            model_id="test-model",
            provider_id=provider.provider_id,
            name="Test Model",
            capabilities=frozenset({"reasoning"}),
            modalities=frozenset({"text"}),
            max_context_tokens=1_000,
            max_complexity=ModelComplexity.COMPLEX,
            supports_tool_calling=True,
        )
    )
    return ModelRouter(models, providers)


def tool_executor(handler: EchoToolHandler | None = None) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="echo",
            name="Echo",
            version="1",
            description="Echo text",
            capabilities=frozenset({"echo"}),
            input_schema={
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            },
            output_schema={
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            },
            handler_id="tests.echo",
        ),
        handler or EchoToolHandler(),
    )
    return ToolExecutor(registry, AllowTestAuthorizer())


def task(max_attempts: int = 1) -> Task:
    return Task(
        task_id="task-1",
        request_id="request-1",
        correlation_id="correlation-1",
        goal="perform bounded work",
        owner="user",
        max_attempts=max_attempts,
    )


class ModelOnlyHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        result = context.invoke_model(context.goal)
        if not result.succeeded:
            assert result.error is not None
            return AgentExecution.failure(result.error, model_results=(result,))
        return AgentExecution.success(
            {"model_content": result.content},
            model_results=(result,),
        )


class ToolOnlyHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        result = context.execute_tool("echo", {"text": context.goal})
        if not result.succeeded:
            assert result.error is not None
            return AgentExecution.failure(result.error, tool_results=(result,))
        return AgentExecution.success(result.output, tool_results=(result,))


class MissingToolHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        result = context.execute_tool("missing", {})
        assert result.error is not None
        return AgentExecution.failure(result.error, tool_results=(result,))


class ModelThenToolHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        model_result = context.invoke_model(context.goal)
        if not model_result.succeeded:
            assert model_result.error is not None
            return AgentExecution.failure(model_result.error, model_results=(model_result,))
        if not model_result.requested_tool_calls:
            return AgentExecution.success(
                {"model_content": model_result.content},
                model_results=(model_result,),
            )
        requested_call = model_result.requested_tool_calls[0]
        tool_result = context.execute_tool(requested_call.tool_id, requested_call.arguments)
        if not tool_result.succeeded:
            assert tool_result.error is not None
            return AgentExecution.failure(
                tool_result.error,
                model_results=(model_result,),
                tool_results=(tool_result,),
            )
        return AgentExecution.success(
            {"model_content": model_result.content, "tool_output": tool_result.output},
            model_results=(model_result,),
            tool_results=(tool_result,),
        )


def runtime(
    handler: ModelOnlyHandler | ToolOnlyHandler | MissingToolHandler | ModelThenToolHandler,
    *,
    provider: DeterministicModelProvider | None = None,
    tools: ToolExecutor | None = None,
) -> AgentRuntime:
    registry = AgentRegistry()
    registry.register(agent_definition(), handler)
    return AgentRuntime(
        registry,
        instance_id_factory=lambda: "instance-1",
        model_invoker=None if provider is None else model_router(provider),
        tool_invoker=tools,
    )


def test_model_only_agent_execution_uses_definition_requirements() -> None:
    provider = DeterministicModelProvider(content="reasoned output")
    work = task()

    result = runtime(ModelOnlyHandler(), provider=provider).execute(work, "service-agent")

    assert result.execution.succeeded
    assert result.execution.value == {"model_content": "reasoned output"}
    assert work.status is TaskStatus.VERIFYING
    assert provider.requests[0].requirements is REQUIREMENTS


def test_tool_only_agent_execution_does_not_require_model() -> None:
    handler = EchoToolHandler()
    work = task()

    result = runtime(ToolOnlyHandler(), tools=tool_executor(handler)).execute(
        work,
        "service-agent",
    )

    assert result.execution.succeeded
    assert result.execution.value == {"text": "perform bounded work"}
    assert handler.calls[0].correlation_id == "correlation-1"


def test_model_tool_flow_requires_explicit_runtime_tool_call() -> None:
    requested = RequestedToolCall("echo", {"text": "from model"})
    provider = DeterministicModelProvider(tool_calls=(requested,))
    tool_handler = EchoToolHandler()
    work = task()

    result = runtime(
        ModelThenToolHandler(),
        provider=provider,
        tools=tool_executor(tool_handler),
    ).execute(work, "service-agent")

    assert result.execution.succeeded
    assert result.execution.value == {
        "model_content": "deterministic model output",
        "tool_output": {"text": "from model"},
    }
    assert len(tool_handler.calls) == 1


def test_model_tool_correlation_ids_propagate_end_to_end() -> None:
    requested = RequestedToolCall("echo", {"text": "trace"})
    provider = DeterministicModelProvider(tool_calls=(requested,))
    tool_handler = EchoToolHandler()

    result = runtime(
        ModelThenToolHandler(),
        provider=provider,
        tools=tool_executor(tool_handler),
    ).execute(task(), "service-agent")

    assert result.execution.succeeded
    model_request = provider.requests[0]
    tool_context = tool_handler.calls[0]
    assert (
        model_request.request_id,
        model_request.task_id,
        model_request.agent_id,
        model_request.instance_id,
        model_request.correlation_id,
    ) == (
        "request-1",
        "task-1",
        "service-agent",
        "instance-1",
        "correlation-1",
    )
    assert tool_context.request_id == model_request.request_id
    assert tool_context.instance_id == model_request.instance_id
    assert tool_context.correlation_id == model_request.correlation_id


def test_model_failure_propagates_to_task_failure() -> None:
    provider = DeterministicModelProvider(
        failure=ErrorInfo("provider_failed", "Provider failed.", "ProviderFailure")
    )
    work = task()

    result = runtime(ModelOnlyHandler(), provider=provider).execute(work, "service-agent")

    assert not result.execution.succeeded
    assert result.execution.error is provider.failure
    assert work.status is TaskStatus.FAILED


def test_model_routing_failure_propagates_to_task_failure() -> None:
    providers = ProviderRegistry()
    providers.register(DeterministicModelProvider())
    empty_router = ModelRouter(ModelRegistry(), providers)
    registry = AgentRegistry()
    registry.register(agent_definition(), ModelOnlyHandler())
    work = task()

    result = AgentRuntime(
        registry,
        instance_id_factory=lambda: "instance-1",
        model_invoker=empty_router,
    ).execute(work, "service-agent")

    assert not result.execution.succeeded
    assert result.execution.error is not None
    assert result.execution.error.code == "no_eligible_model"
    assert work.status is TaskStatus.FAILED


def test_tool_failure_propagates_to_task_failure() -> None:
    work = task()

    result = runtime(MissingToolHandler(), tools=tool_executor()).execute(
        work,
        "service-agent",
    )

    assert not result.execution.succeeded
    assert result.execution.error is not None
    assert result.execution.error.code == "tool_not_found"
    assert work.status is TaskStatus.FAILED


def test_model_output_does_not_automatically_execute_requested_tool() -> None:
    provider = DeterministicModelProvider(
        tool_calls=(RequestedToolCall("echo", {"text": "not authorized"}),)
    )
    tool_handler = EchoToolHandler()

    result = runtime(
        ModelOnlyHandler(),
        provider=provider,
        tools=tool_executor(tool_handler),
    ).execute(task(), "service-agent")

    assert result.execution.succeeded
    assert result.execution.model_results[0].requested_tool_calls
    assert tool_handler.calls == []


def test_executive_and_existing_structural_verification_accept_integrated_result() -> None:
    provider = DeterministicModelProvider(content="verified structure")
    executive = ZyroExecutive(
        runtime(ModelOnlyHandler(), provider=provider),
        verifier=StructuralRuntimeVerifier(),
        id_factory=lambda: "generated-id",
    )

    result = executive.handle(
        UserRequest(
            goal="work",
            requester="user",
            agent_id="service-agent",
            request_id="request-1",
            correlation_id="correlation-1",
        )
    )

    assert result.outcome is ExecutiveOutcome.VERIFIED_SUCCESS
    assert result.task_status is TaskStatus.DONE
