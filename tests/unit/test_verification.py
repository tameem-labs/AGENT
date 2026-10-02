from zyro.agents.handler import AgentExecution
from zyro.agents.instance import AgentInstance
from zyro.core.task import Task
from zyro.execution.verification import StructuralRuntimeVerifier, VerificationOutcome
from zyro.runtime.agent_runtime import RuntimeExecution


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
