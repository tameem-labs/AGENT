from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.executive import ExecutiveOutcome, UserRequest, ZyroExecutive
from zyro.core.task import Task, TaskStatus
from zyro.execution.verification import StructuralRuntimeVerifier
from zyro.observability import OperationalObserver, SQLiteObservabilityStore, TraceQuery
from zyro.recovery import FailureIdentity, RecoveryAction, RecoveryPolicy, RecoveryRequest
from zyro.resources import (
    ResourcePolicy,
    ResourceRecoveryBridge,
    SQLiteResourceManager,
    UsagePrecision,
)
from zyro.runtime.agent_runtime import AgentRuntime

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


class SuccessHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        return AgentExecution.success({"task": context.task_id, "status": "complete"})


def test_request_task_agent_execution_verification_trace(tmp_path: Path) -> None:
    trace_store = SQLiteObservabilityStore(tmp_path / "trace.sqlite")
    sequence = count(1)
    observer = OperationalObserver(
        trace_store,
        clock=lambda: NOW,
        id_factory=lambda: f"trace-{next(sequence)}",
    )
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            "agent-1",
            "Agent",
            "1.0",
            "bounded",
            "test",
            ("execute",),
            ("test",),
        ),
        SuccessHandler(),
    )
    runtime = AgentRuntime(registry, instance_id_factory=lambda: "instance-1", observer=observer)
    executive = ZyroExecutive(
        runtime,
        StructuralRuntimeVerifier(),
        id_factory=iter(("task-1",)).__next__,
        observer=observer,
    )

    result = executive.handle(
        UserRequest(
            "Perform bounded work",
            "owner-1",
            "agent-1",
            request_id="request-1",
            correlation_id="correlation-1",
        )
    )
    trace = trace_store.query(TraceQuery(correlation_id="correlation-1"))

    assert result.outcome is ExecutiveOutcome.VERIFIED_SUCCESS
    assert result.task_status is TaskStatus.DONE
    assert [item.event_type for item in trace] == [
        "REQUEST_RECEIVED",
        "TASK_CREATED",
        "AGENT_DISPATCHED",
        "EXECUTION_COMPLETED",
        "VERIFICATION_STARTED",
        "VERIFICATION_COMPLETED",
        "TASK_COMPLETED",
    ]
    assert all(item.request_id == "request-1" for item in trace)
    assert all(item.task_id == "task-1" for item in trace)
    assert trace[-1].verification_id is not None
    trace_store.close()


def test_resource_hard_stop_preserves_task_and_records_honest_recovery(
    tmp_path: Path,
) -> None:
    task = Task("task-1", "request-1", "correlation-1", "Bounded work", "owner-1")
    manager = SQLiteResourceManager(
        tmp_path / "resources.sqlite",
        ResourcePolicy(task_token_limit=5),
        clock=lambda: NOW,
    )
    result = manager.consume_tokens("task-1", None, 6, UsagePrecision.EXACT)
    failure = ResourceRecoveryBridge(
        clock=lambda: NOW, id_factory=lambda: "failure-resource"
    ).failure(result, FailureIdentity("request-1", "task-1", "correlation-1"))
    assert failure is not None

    decision = RecoveryPolicy(clock=lambda: NOW).decide(
        RecoveryRequest(
            "recovery-resource",
            "operation-resource",
            failure,
            0,
            3,
            True,
            True,
            True,
            False,
            False,
            task.status.value,
        )
    )

    assert decision.action is RecoveryAction.STOP
    assert task.status is TaskStatus.PENDING  # Resource Manager did not seize Task authority.
    assert task.attempt_count == 0
    assert manager.hard_stop_count("task-1") == 1
    manager.close()


def test_observability_failure_cannot_mutate_runtime_behavior() -> None:
    class BrokenObserver:
        def record(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("telemetry unavailable")

    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            "agent-1",
            "Agent",
            "1.0",
            "bounded",
            "test",
            ("execute",),
            ("test",),
        ),
        SuccessHandler(),
    )
    runtime = AgentRuntime(
        registry,
        instance_id_factory=lambda: "instance-1",
        observer=BrokenObserver(),  # type: ignore[arg-type]
    )
    task = Task("task-1", "request-1", "correlation-1", "Goal", "owner-1")

    result = runtime.execute(task, "agent-1")

    assert result.execution.succeeded
    assert task.status is TaskStatus.VERIFYING
