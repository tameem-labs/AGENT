from zyro.agents.handler import AgentExecution
from zyro.agents.instance import AgentInstance
from zyro.core.errors import ErrorInfo
from zyro.core.task import Task
from zyro.execution.verification import StructuralRuntimeVerifier, VerificationOutcome
from zyro.runtime.agent_runtime import RuntimeExecution
from zyro.tools.contracts import ToolResult, ToolResultStatus


def test_structural_verifier_rejects_inconsistent_runtime_evidence() -> None:
    task = Task(
        task_id="task-1",
        request_id="request-1",
        correlation_id="correlation-1",
        goal="work",
        owner="user",
    )
    task.start()
    task.record_execution_success("task-result")
    instance = AgentInstance(
        "instance-1",
        "agent-1",
        task.task_id,
        task.request_id,
        task.correlation_id,
    )
    instance.start()
    instance.succeed("different-result")

    result = StructuralRuntimeVerifier().verify(
        task,
        RuntimeExecution(instance, AgentExecution.success("different-result")),
    )

    assert result.outcome is VerificationOutcome.FAILED
    assert result.error is not None
    assert result.error.retryable


def test_structural_verification_carries_evidence_and_identities() -> None:
    task = Task("task-1", "request-1", "correlation-1", "work", "user")
    task.start()
    task.record_execution_success("result")
    instance = AgentInstance(
        "instance-1",
        "agent-1",
        task.task_id,
        task.request_id,
        task.correlation_id,
    )
    instance.start()
    instance.succeed("result")

    result = StructuralRuntimeVerifier().verify(
        task,
        RuntimeExecution(instance, AgentExecution.success("result")),
    )

    assert result.outcome is VerificationOutcome.VERIFIED
    assert result.verification_id is not None
    assert result.task_id == "task-1"
    assert result.execution_id == "instance-1"
    assert result.verifier_id == "structural-runtime-verifier"
    assert result.verified_at is not None
    assert result.evidence[0].evidence_type == "runtime_consistency"
    assert result.scope == "runtime_structure"


def test_structural_verifier_preserves_unknown_execution_as_unknown() -> None:
    task = Task("task-1", "request-1", "correlation-1", "work", "user")
    task.start()
    error = ErrorInfo(
        "external_outcome_unknown",
        "Completion cannot be established.",
        "UnknownExternalOutcome",
    )
    task.record_execution_unknown(None, error)
    tool_result = ToolResult(
        status=ToolResultStatus.UNKNOWN,
        tool_id="writer",
        request_id="request-1",
        task_id="task-1",
        agent_id="agent-1",
        instance_id="instance-1",
        correlation_id="correlation-1",
        error=error,
    )
    instance = AgentInstance(
        "instance-1",
        "agent-1",
        task.task_id,
        task.request_id,
        task.correlation_id,
    )
    instance.start()
    instance.mark_unknown(None, error)
    execution = AgentExecution.failure(error, tool_results=(tool_result,))

    result = StructuralRuntimeVerifier().verify(task, RuntimeExecution(instance, execution))

    assert result.outcome is VerificationOutcome.UNKNOWN
    assert result.error is None
    assert result.evidence[0].evidence_type == "unknown_execution_outcome"
