from __future__ import annotations

from pathlib import Path
from time import sleep

from tests.fakes import AllowTestAuthorizer
from zyro.core.risk import RiskClass
from zyro.tools.contracts import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolHandlerResult,
    ToolResultStatus,
)
from zyro.tools.executor import ToolExecutor
from zyro.tools.registry import ToolRegistry
from zyro.workflows import (
    StepExecution,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStore,
)


class SlowSideEffectHandler:
    def execute(self, context: ToolExecutionContext, arguments: object) -> ToolHandlerResult:
        sleep(0.05)
        return ToolHandlerResult.success({"completed": True})


def test_configured_tool_timeout_is_enforced_as_unknown_not_safe_retry() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "slow",
            "Slow tool",
            "1",
            "Blocking operation",
            frozenset({"slow.run"}),
            {"type": "object", "properties": {}},
            {
                "type": "object",
                "properties": {"completed": {"type": "boolean"}},
                "required": ["completed"],
            },
            "slow-handler",
            RiskClass.AUTOMATIC,
            timeout_seconds=0.005,
        ),
        SlowSideEffectHandler(),
    )
    result = ToolExecutor(registry, AllowTestAuthorizer()).execute(
        ToolCall("slow", {}, "request", "task", "agent", "instance", "correlation")
    )
    assert result.status is ToolResultStatus.UNKNOWN
    assert result.error is not None
    assert result.error.error_type == "ExternalSideEffectUncertainty"


def test_workflow_dependencies_controls_and_restart_recovery(tmp_path: Path) -> None:
    path = tmp_path / "workflow.sqlite"
    store = WorkflowStore(path)
    definition = WorkflowDefinition(
        "workflow-1",
        "request-1",
        "correlation-1",
        "owner-1",
        "Run two bounded steps",
        (
            WorkflowStep("first", "First", "safe", "agent-1"),
            WorkflowStep("second", "Second", "safe", "agent-1", ("first",)),
        ),
    )
    store.create(definition)
    engine = WorkflowEngine(
        store, {"safe": lambda workflow, step: StepExecution(True, step.step_id)}
    )
    completed = engine.run("workflow-1")
    assert completed.status is WorkflowStatus.COMPLETED
    assert list(completed.step_statuses) == ["first", "second"]
    store.close()

    reopened = WorkflowStore(path)
    assert reopened.get("workflow-1").status is WorkflowStatus.COMPLETED
    reopened.close()
