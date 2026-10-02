import pytest

from zyro.agents.definition import AgentDefinition, RiskClass
from zyro.agents.instance import AgentInstance, AgentInstanceStatus
from zyro.core.errors import ErrorInfo, InvalidAgentError, InvalidAgentTransition


def make_definition() -> AgentDefinition:
    return AgentDefinition(
        agent_id="echo-agent",
        name="Echo Agent",
        version="1.0.0",
        role="Return bounded deterministic output",
        domain="core",
        responsibilities=("Process a supplied goal",),
        capabilities=("echo",),
        permissions=(),
        risk_class=RiskClass.AUTOMATIC,
        input_requirements=("goal",),
        output_contract={"type": "mapping"},
        context_requirements=("task",),
        communication_rules=("return structured output",),
        model_requirements=(),
        verification_requirements=("runtime structure",),
        resource_limits={"max_attempts": 2},
    )


def test_agent_definition_contains_immutable_contract_metadata() -> None:
    definition = make_definition()

    assert definition.agent_id == "echo-agent"
    assert definition.risk_class is RiskClass.AUTOMATIC
    with pytest.raises(TypeError):
        definition.resource_limits["max_attempts"] = 9  # type: ignore[index]


def test_invalid_agent_definition_is_rejected() -> None:
    with pytest.raises(InvalidAgentError, match="responsibility"):
        AgentDefinition(
            agent_id="bad",
            name="Bad",
            version="1",
            role="none",
            domain="core",
            responsibilities=(),
            capabilities=("nothing",),
        )


def test_pending_agent_instance_can_be_cancelled() -> None:
    instance = AgentInstance(
        instance_id="instance-cancelled",
        agent_id="echo-agent",
        task_id="task-1",
        request_id="request-1",
        correlation_id="correlation-1",
    )

    instance.cancel()

    assert instance.status is AgentInstanceStatus.CANCELLED
    assert instance.completed_at is not None


def test_agent_instance_lifecycle_is_separate_from_definition() -> None:
    definition = make_definition()
    instance = AgentInstance(
        instance_id="instance-1",
        agent_id=definition.agent_id,
        task_id="task-1",
        request_id="request-1",
        correlation_id="correlation-1",
    )

    instance.start()
    instance.succeed({"value": "done"})

    assert instance.status is AgentInstanceStatus.SUCCEEDED
    assert instance.result == {"value": "done"}
    assert instance.started_at is not None
    assert instance.completed_at is not None
    with pytest.raises(InvalidAgentTransition):
        instance.fail(ErrorInfo("late", "Too late", "StateError"))
