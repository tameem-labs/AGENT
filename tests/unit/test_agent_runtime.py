import json
from io import StringIO

import pytest

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.instance import AgentInstanceStatus
from zyro.agents.registry import AgentRegistry
from zyro.core.errors import ErrorInfo, InvalidTaskTransition
from zyro.core.logging import configure_logging
from zyro.core.task import Task, TaskStatus
from zyro.runtime.agent_runtime import AgentRuntime


class SuccessfulHandler:
    def __init__(self) -> None:
        self.context: ExecutionContext | None = None

    def execute(self, context: ExecutionContext) -> AgentExecution:
        self.context = context
        return AgentExecution.success({"processed": context.goal})


class RaisingHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        raise RuntimeError(f"cannot process {context.task_id}")


def definition(agent_id: str = "agent-1") -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        name="Test Agent",
        version="1",
        role="test",
        domain="core",
        responsibilities=("test bounded execution",),
        capabilities=("test",),
    )


def task() -> Task:
    return Task(
        task_id="task-1",
        request_id="request-1",
        correlation_id="correlation-1",
        goal="do work",
        owner="user-1",
    )


def test_runtime_executes_agent_and_preserves_all_correlation_ids() -> None:
    registry = AgentRegistry()
    handler = SuccessfulHandler()
    registry.register(definition(), handler)
    runtime = AgentRuntime(registry, instance_id_factory=lambda: "instance-1")
    work = task()

    result = runtime.execute(work, "agent-1")

    assert result.execution.succeeded
    assert result.instance is not None
    assert result.instance.status is AgentInstanceStatus.SUCCEEDED
    assert work.status is TaskStatus.VERIFYING
    assert handler.context == ExecutionContext(
        task_id="task-1",
        request_id="request-1",
        correlation_id="correlation-1",
        agent_id="agent-1",
        instance_id="instance-1",
        goal="do work",
        attempt=1,
    )


def test_runtime_logs_the_existing_correlation_identifiers() -> None:
    output = StringIO()
    configure_logging("INFO", output)
    registry = AgentRegistry()
    registry.register(definition(), SuccessfulHandler())
    runtime = AgentRuntime(registry, instance_id_factory=lambda: "instance-1")

    runtime.execute(task(), "agent-1")

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert records
    for record in records:
        assert record["request_id"] == "request-1"
        assert record["task_id"] == "task-1"
        assert record["agent_id"] == "agent-1"
        assert record["instance_id"] == "instance-1"
        assert record["correlation_id"] == "correlation-1"


def test_missing_agent_returns_structured_failure() -> None:
    work = task()
    result = AgentRuntime(AgentRegistry()).execute(work, "missing")

    assert not result.execution.succeeded
    assert result.instance is None
    assert result.execution.error is not None
    assert result.execution.error.code == "agent_not_found"
    assert work.status is TaskStatus.FAILED


def test_handler_exception_is_not_reported_as_success() -> None:
    registry = AgentRegistry()
    registry.register(definition(), RaisingHandler())
    work = task()

    result = AgentRuntime(registry).execute(work, "agent-1")

    assert not result.execution.succeeded
    assert result.execution.error is not None
    assert result.execution.error.error_type == "RuntimeError"
    assert result.instance is not None
    assert result.instance.status is AgentInstanceStatus.FAILED
    assert work.status is TaskStatus.FAILED


def test_duplicate_execution_attempt_is_rejected() -> None:
    registry = AgentRegistry()
    registry.register(definition(), SuccessfulHandler())
    runtime = AgentRuntime(registry)
    work = task()
    runtime.execute(work, "agent-1")

    with pytest.raises(InvalidTaskTransition, match="assigned before an attempt"):
        runtime.execute(work, "agent-1")


def test_agent_execution_requires_error_on_failure() -> None:
    with pytest.raises(ValueError, match="must contain an error"):
        AgentExecution(succeeded=False)

    error = ErrorInfo("failed", "failed", "Failure")
    assert AgentExecution.failure(error).error is error
