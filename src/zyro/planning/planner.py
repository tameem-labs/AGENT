"""Dynamic Goal Decomposition and Planning subsystem for ZYRO."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from zyro.agents.registry import AgentRegistry
from zyro.planning.contracts import Plan, PlanIntent, PlanStep
from zyro.tools.registry import ToolRegistry
from zyro.workflows.contracts import TriggerKind, WorkflowDefinition, WorkflowStep


class BrainPlanner:
    """Decomposes arbitrary user goals into structured, validated execution DAGs."""

    def __init__(
        self,
        agents: AgentRegistry,
        tools: ToolRegistry,
    ) -> None:
        self._agents = agents
        self._tools = tools

    def plan(
        self,
        goal: str,
        owner_id: str = "local-owner",
        *,
        context_summary: str | None = None,
    ) -> Plan:
        clean = goal.strip()
        if not clean:
            raise ValueError("goal must be a non-empty string")

        intent = self._detect_intent(clean)
        steps = self._generate_steps_for_intent(clean, intent)

        # Validate that all assigned agents exist in AgentRegistry
        registered_agent_ids = {agent.agent_id for agent in self._agents.list()}
        processed_steps: list[PlanStep] = []
        for step in steps:
            # Fallback to zyro.executive if specialized agent is not registered
            if (
                step.agent_id not in registered_agent_ids
                and "zyro.executive" in registered_agent_ids
            ):
                processed_steps.append(
                    PlanStep(
                        step_id=step.step_id,
                        name=step.name,
                        description=step.description,
                        capability="assistant.respond",
                        agent_id="zyro.executive",
                        tools=step.tools,
                        dependencies=step.dependencies,
                        permissions=step.permissions,
                        approval_required=step.approval_required,
                        verification_plan=step.verification_plan,
                        timeout_seconds=step.timeout_seconds,
                        max_attempts=step.max_attempts,
                    )
                )
            else:
                processed_steps.append(step)

        plan = Plan(
            plan_id=f"plan-{uuid4().hex[:12]}",
            objective=clean,
            intent=intent,
            assumptions=(
                "Local environment conforms to execution security policies",
                "Integrations status verified at step execution time",
            ),
            constraints=(
                "Consequential external writes require explicit human approval",
                "Execution results require independent verification",
            ),
            confidence=0.95,
            steps=tuple(processed_steps),
            created_at=datetime.now(UTC),
        )
        plan.validate_dag()
        return plan

    @staticmethod
    def _detect_intent(goal: str) -> PlanIntent:
        lower = goal.lower()
        inspection_keywords = (
            "check this project",
            "inspect code",
            "run tests",
            "run linter",
            "analyze repository",
            "check my computer",
        )
        if any(keyword in lower for keyword in inspection_keywords):
            return PlanIntent.CODING_INSPECTION

        dev_keywords = (
            "fix bug",
            "implement feature",
            "refactor code",
            "write a function",
            "create branch",
        )
        if any(keyword in lower for keyword in dev_keywords):
            return PlanIntent.CODING_DEVELOPMENT

        freelance_discovery_keywords = (
            "find leads",
            "potential clients",
            "freelance clients",
            "find 20",
            "discover leads",
        )
        if any(keyword in lower for keyword in freelance_discovery_keywords):
            return PlanIntent.FREELANCE_DISCOVERY

        freelance_outreach_keywords = ("send outreach", "contact client", "email lead")
        if any(keyword in lower for keyword in freelance_outreach_keywords):
            return PlanIntent.FREELANCE_OUTREACH

        content_publishing_keywords = ("publish to instagram", "publish post", "post content")
        if any(keyword in lower for keyword in content_publishing_keywords):
            return PlanIntent.CONTENT_PUBLISHING

        content_creation_keywords = (
            "content idea",
            "write script",
            "create content",
            "script idea",
            "content brief",
        )
        if any(keyword in lower for keyword in content_creation_keywords):
            return PlanIntent.CONTENT_CREATION

        personal_keywords = (
            "calendar",
            "reminder",
            "agenda",
            "schedule focus",
            "my schedule",
        )
        if any(keyword in lower for keyword in personal_keywords):
            return PlanIntent.PERSONAL_TASK

        ops_keywords = (
            "system health",
            "database integrity",
            "backup status",
            "check services",
        )
        if any(keyword in lower for keyword in ops_keywords):
            return PlanIntent.OPERATIONS_CHECK

        if lower.startswith("research ") or "research this" in lower or "latest info" in lower:
            return PlanIntent.RESEARCH

        if " and then " in lower or ("first " in lower and " then " in lower):
            return PlanIntent.MULTI_STEP_WORKFLOW

        return PlanIntent.CONVERSATION

    def _generate_steps_for_intent(self, goal: str, intent: PlanIntent) -> tuple[PlanStep, ...]:
        if intent is PlanIntent.CODING_INSPECTION:
            return (
                PlanStep(
                    step_id="discover-project",
                    name="Project Discovery",
                    description="Inspect authorized project structure and detect configuration",
                    capability="coding.discover",
                    agent_id="coding.discovery",
                    tools=("coding.project_discovery",),
                ),
                PlanStep(
                    step_id="analyze-code",
                    name="Code & AST Analysis",
                    description="Perform AST analysis and identify structural anomalies",
                    capability="coding.analyze",
                    agent_id="coding.analysis",
                    tools=("coding.ast_analysis", "coding.inspect_file"),
                    dependencies=("discover-project",),
                ),
                PlanStep(
                    step_id="run-tests-and-lint",
                    name="Run Test Suite & Linter",
                    description="Execute sandboxed tests and linters to observe actual failures",
                    capability="coding.test",
                    agent_id="coding.testing",
                    tools=("coding.run_tests", "coding.run_linter"),
                    dependencies=("discover-project",),
                ),
                PlanStep(
                    step_id="review-and-report",
                    name="Review & Diagnosis Report",
                    description="Synthesize code analysis and test results into verified diagnosis",
                    capability="coding.review",
                    agent_id="coding.review",
                    tools=(),
                    dependencies=("analyze-code", "run-tests-and-lint"),
                    verification_plan="test_and_linter_evidence",
                ),
            )

        if intent is PlanIntent.CODING_DEVELOPMENT:
            return (
                PlanStep(
                    step_id="analyze-requirement",
                    name="Analyze Development Requirement",
                    description="Analyze code change requirements and locate target files",
                    capability="coding.analyze",
                    agent_id="coding.analysis",
                    tools=("coding.inspect_file", "coding.search_code"),
                ),
                PlanStep(
                    step_id="apply-code-changes",
                    name="Apply Code Changes",
                    description="Apply sandboxed file modifications",
                    capability="coding.edit",
                    agent_id="coding.development",
                    tools=("coding.edit_file",),
                    dependencies=("analyze-requirement",),
                    approval_required=True,
                ),
                PlanStep(
                    step_id="verify-changes",
                    name="Verify Code Changes",
                    description="Run tests on modified code and ensure regression-free status",
                    capability="coding.test",
                    agent_id="coding.testing",
                    tools=("coding.run_tests", "coding.run_linter"),
                    dependencies=("apply-code-changes",),
                    verification_plan="test_exit_code_zero",
                ),
            )

        if intent is PlanIntent.FREELANCE_DISCOVERY:
            return (
                PlanStep(
                    step_id="discover-leads",
                    name="Discover Potential Leads",
                    description="Search public sources and permitted APIs for prospective clients",
                    capability="freelancing.discover",
                    agent_id="freelancing.discovery",
                    tools=("freelancing.discover_leads", "research.web_search"),
                ),
                PlanStep(
                    step_id="qualify-and-score",
                    name="Qualify & Score Leads",
                    description="Validate and score discovered leads against ideal client criteria",
                    capability="freelancing.qualify",
                    agent_id="freelancing.qualification",
                    tools=(),
                    dependencies=("discover-leads",),
                ),
            )

        if intent is PlanIntent.FREELANCE_OUTREACH:
            return (
                PlanStep(
                    step_id="prepare-outreach",
                    name="Prepare Outreach Copy",
                    description="Draft personalized outreach proposal for qualified lead",
                    capability="freelancing.prepare_outreach",
                    agent_id="freelancing.outreach",
                    tools=(),
                ),
                PlanStep(
                    step_id="dispatch-outreach",
                    name="Dispatch Outreach Email",
                    description="Dispatch approved outreach email through connected account",
                    capability="freelancing.dispatch",
                    agent_id="freelancing.outreach",
                    tools=("email.send",),
                    dependencies=("prepare-outreach",),
                    approval_required=True,
                    verification_plan="provider_message_id_evidence",
                ),
            )

        if intent is PlanIntent.CONTENT_CREATION:
            return (
                PlanStep(
                    step_id="research-trends",
                    name="Research Trends & Angles",
                    description="Explore current topic trends and unique angles",
                    capability="content.research",
                    agent_id="content.trend",
                    tools=("research.web_search",),
                ),
                PlanStep(
                    step_id="generate-script",
                    name="Draft Script & Brief",
                    description="Produce structured content brief and script",
                    capability="content.script",
                    agent_id="content.script",
                    tools=(),
                    dependencies=("research-trends",),
                ),
            )

        if intent is PlanIntent.CONTENT_PUBLISHING:
            return (
                PlanStep(
                    step_id="prepare-publication",
                    name="Prepare Publication Assets",
                    description="Format post content and platform-specific metadata",
                    capability="content.script",
                    agent_id="content.script",
                ),
                PlanStep(
                    step_id="publish-post",
                    name="Publish to External Platform",
                    description="Publish approved content to connected social platform",
                    capability="content.publish",
                    agent_id="content.publishing",
                    tools=("content.publish_post",),
                    dependencies=("prepare-publication",),
                    approval_required=True,
                    verification_plan="provider_post_id_evidence",
                ),
            )

        if intent is PlanIntent.PERSONAL_TASK:
            return (
                PlanStep(
                    step_id="organize-agenda",
                    name="Personal Agenda Organization",
                    description="Organize schedule, reminders and priorities",
                    capability="personal.organize",
                    agent_id="personal.organizer",
                    tools=("personal.list_agenda", "personal.create_reminder"),
                ),
            )

        if intent is PlanIntent.OPERATIONS_CHECK:
            return (
                PlanStep(
                    step_id="check-system-health",
                    name="System & Database Health Check",
                    description="Inspect SQLite store integrity and service connectivity",
                    capability="operations.health_check",
                    agent_id="operations.health",
                    tools=("operations.database_check",),
                    verification_plan="pragma_quick_check_ok",
                ),
            )

        if intent is PlanIntent.RESEARCH:
            return (
                PlanStep(
                    step_id="research-and-report",
                    name="Search, Compare, Verify & Synthesize",
                    description="Collect evidence, verify digests, and produce comparison",
                    capability="research.execute",
                    agent_id="zyro.research",
                    tools=("research.web_search", "research.read_source"),
                    verification_plan="source_content_digest_and_timestamp",
                ),
            )

        # Default conversational step
        return (
            PlanStep(
                step_id="understand-and-respond",
                name="Understand & Respond",
                description="Interpret user request and coordinate appropriate response",
                capability="assistant.respond",
                agent_id="zyro.executive",
            ),
        )

    @staticmethod
    def compile_to_workflow(
        plan: Plan,
        request_id: str,
        correlation_id: str,
        owner_principal_id: str,
        trigger: TriggerKind = TriggerKind.IMMEDIATE,
        workflow_id: str | None = None,
    ) -> WorkflowDefinition:
        workflow_steps = tuple(
            WorkflowStep(
                step_id=step.step_id,
                name=step.name,
                capability=step.capability,
                agent_id=step.agent_id,
                dependencies=step.dependencies,
                max_attempts=step.max_attempts,
                requires_approval=step.approval_required,
            )
            for step in plan.steps
        )
        return WorkflowDefinition(
            workflow_id=workflow_id or f"wf-{uuid4().hex[:12]}",
            request_id=request_id,
            correlation_id=correlation_id,
            owner_principal_id=owner_principal_id,
            goal=plan.objective,
            steps=workflow_steps,
            trigger=trigger,
            created_at=datetime.now(UTC),
        )


__all__ = ["BrainPlanner"]
