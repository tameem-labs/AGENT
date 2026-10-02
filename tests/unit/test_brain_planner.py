from __future__ import annotations

import pytest

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.risk import RiskClass
from zyro.planning import BrainPlanner, PlanIntent, PlanStep
from zyro.tools.registry import ToolRegistry


class DummyHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        return AgentExecution.success({"message": "ok"})


def build_planner() -> BrainPlanner:
    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            "zyro.executive",
            "Executive",
            "1.0.0",
            "Executive agent",
            "core",
            ("chat",),
            ("assistant.respond",),
            risk_class=RiskClass.AUTOMATIC,
        ),
        DummyHandler(),
    )
    agents.register(
        AgentDefinition(
            "zyro.research",
            "Research",
            "1.0.0",
            "Research agent",
            "research",
            ("research",),
            ("research.execute",),
            risk_class=RiskClass.AUTOMATIC,
        ),
        DummyHandler(),
    )
    tools = ToolRegistry()
    return BrainPlanner(agents, tools)


def test_planner_detects_research_intent() -> None:
    planner = build_planner()
    plan = planner.plan("Research the latest advancements in AI agents")
    assert plan.intent is PlanIntent.RESEARCH
    assert len(plan.steps) == 1
    assert plan.steps[0].agent_id == "zyro.research"
    assert plan.steps[0].capability == "research.execute"


def test_planner_detects_coding_inspection_intent() -> None:
    planner = build_planner()
    plan = planner.plan("Check this project on my computer and tell me what is wrong")
    assert plan.intent is PlanIntent.CODING_INSPECTION
    assert len(plan.steps) == 4
    # Check DAG dependencies
    step_map = {step.step_id: step for step in plan.steps}
    assert "discover-project" in step_map
    assert "analyze-code" in step_map
    assert "run-tests-and-lint" in step_map
    assert "review-and-report" in step_map
    assert step_map["analyze-code"].dependencies == ("discover-project",)
    assert step_map["run-tests-and-lint"].dependencies == ("discover-project",)
    assert set(step_map["review-and-report"].dependencies) == {"analyze-code", "run-tests-and-lint"}


def test_planner_detects_freelance_discovery() -> None:
    planner = build_planner()
    plan = planner.plan("Find 20 potential freelance clients for web development")
    assert plan.intent is PlanIntent.FREELANCE_DISCOVERY
    assert len(plan.steps) == 2
    assert plan.steps[0].step_id == "discover-leads"
    assert plan.steps[1].step_id == "qualify-and-score"
    assert plan.steps[1].dependencies == ("discover-leads",)


def test_planner_detects_freelance_outreach_requires_approval() -> None:
    planner = build_planner()
    plan = planner.plan("Send outreach to prospective client Acme Corp")
    assert plan.intent is PlanIntent.FREELANCE_OUTREACH
    assert len(plan.steps) == 2
    assert plan.steps[1].approval_required is True


def test_planner_compiles_to_workflow() -> None:
    planner = build_planner()
    plan = planner.plan("Research quantum computing breakthroughs")
    workflow = planner.compile_to_workflow(
        plan, "req-123", "corr-456", "local-owner"
    )
    assert workflow.goal == plan.objective
    assert workflow.request_id == "req-123"
    assert workflow.correlation_id == "corr-456"
    assert workflow.owner_principal_id == "local-owner"
    assert len(workflow.steps) == 1
    assert workflow.steps[0].step_id == plan.steps[0].step_id


def test_plan_dag_validation_catches_cycles() -> None:
    step1 = PlanStep("s1", "Step 1", "Desc", "cap", "agent", dependencies=("s2",))
    step2 = PlanStep("s2", "Step 2", "Desc", "cap", "agent", dependencies=("s1",))
    with pytest.raises(ValueError, match="cyclic dependencies"):
        from zyro.planning.contracts import Plan
        Plan("p1", "Cyclic goal", PlanIntent.CONVERSATION, steps=(step1, step2))
