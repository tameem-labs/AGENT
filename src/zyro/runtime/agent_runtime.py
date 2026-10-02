"""Minimal provider-independent runtime for one bounded agent attempt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from zyro.agents.handler import AgentExecution, ContextProvider, ExecutionContext
from zyro.agents.instance import AgentInstance
from zyro.agents.registry import AgentRegistry
from zyro.core.errors import ErrorInfo, MissingAgentError
from zyro.core.logging import LogContext, get_logger
from zyro.core.task import Task
from zyro.models.contracts import ModelInvoker
from zyro.tools.contracts import ToolInvoker


@dataclass(frozen=True, slots=True)
class RuntimeExecution:
    instance: AgentInstance | None
    execution: AgentExecution


class AgentRuntime:
    """Starts agent instances and maps execution outcomes to task state."""

    def __init__(
        self,
        registry: AgentRegistry,
        instance_id_factory: Callable[[], str] | None = None,
        model_invoker: ModelInvoker | None = None,
        tool_invoker: ToolInvoker | None = None,
        context_provider: ContextProvider | None = None,
    ) -> None:
        self._registry = registry
        self._instance_id_factory = instance_id_factory or (lambda: str(uuid4()))
        self._model_invoker = model_invoker
        self._tool_invoker = tool_invoker
        self._context_provider = context_provider

    def execute(self, task: Task, agent_id: str) -> RuntimeExecution:
        """Execute exactly one task attempt; invalid duplicate attempts are rejected."""
        task.assign_agent(agent_id)
        task.start()
        try:
            registered = self._registry.get(agent_id)
        except MissingAgentError as error:
            failure = ErrorInfo(
                code="agent_not_found",
                message=str(error),
                error_type=type(error).__name__,
            )
            task.fail(failure)
            return RuntimeExecution(None, AgentExecution.failure(failure))

        instance = AgentInstance(
            instance_id=self._instance_id_factory(),
            agent_id=registered.definition.agent_id,
            task_id=task.task_id,
            request_id=task.request_id,
            correlation_id=task.correlation_id,
        )
        instance.start()
        context = ExecutionContext(
            task_id=task.task_id,
            request_id=task.request_id,
            correlation_id=task.correlation_id,
            agent_id=agent_id,
            instance_id=instance.instance_id,
            goal=task.goal,
            attempt=task.attempt_count,
            model_invoker=self._model_invoker,
            tool_invoker=self._tool_invoker,
            default_model_requirements=registered.definition.model_requirements,
            requester_id=task.owner,
            pending_approval_id=task.pending_approval_id,
            context_provider=self._context_provider,
        )
        logger = get_logger(
            "agent_runtime",
            LogContext(
                request_id=task.request_id,
                task_id=task.task_id,
                agent_id=agent_id,
                instance_id=instance.instance_id,
                correlation_id=task.correlation_id,
            ),
        )
        logger.info("agent execution started")
        try:
            execution = registered.handler.execute(context)
        except Exception as error:
            failure = ErrorInfo(
                code="agent_execution_exception",
                message=f"Agent handler raised {type(error).__name__}.",
                error_type=type(error).__name__,
            )
            instance.fail(failure)
            task.fail(failure)
            # Exception content is deliberately excluded because handlers may process secrets.
            logger.error("agent execution raised an exception")
            return RuntimeExecution(instance, AgentExecution.failure(failure))

        if execution.succeeded:
            instance.succeed(execution.value)
            task.record_execution_success(execution.value)
            logger.info("agent execution succeeded; verification required")
        elif execution.waiting_for_approval:
            assert execution.error is not None
            approval_id = execution.pending_approval_id
            if approval_id is None:
                failure = ErrorInfo(
                    code="approval_contract_invalid",
                    message="Approval-pending result omitted its approval identity.",
                    error_type="ApprovalContractInvalid",
                )
                instance.fail(failure)
                task.fail(failure)
            else:
                instance.wait_for_approval(execution.error)
                task.wait_for_approval(approval_id)
                logger.info("agent execution is waiting for approval")
        elif execution.outcome_unknown:
            assert execution.error is not None
            instance.mark_unknown(execution.value, execution.error)
            task.record_execution_unknown(execution.value, execution.error)
            logger.warning("agent execution outcome is unknown; verification required")
        else:
            assert execution.error is not None
            instance.fail(execution.error)
            task.fail(execution.error)
            logger.warning("agent execution failed")
        return RuntimeExecution(instance, execution)
