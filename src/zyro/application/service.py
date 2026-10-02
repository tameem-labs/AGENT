"""Canonical application service used by the localhost API and UI."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.application.store import ApplicationStore
from zyro.core.executive import UserRequest, ZyroExecutive
from zyro.core.risk import RiskClass
from zyro.execution.verification import StructuralRuntimeVerifier
from zyro.integrations import IntegrationService
from zyro.models.contracts import (
    ModelComplexity,
    ModelDefinition,
    ModelDeployment,
    ModelRequest,
    ModelRequirements,
    ModelResult,
    ModelResultStatus,
    ModelUsage,
)
from zyro.models.gemini import GeminiProvider
from zyro.models.router import build_model_router
from zyro.resources import ResourceAwareModelInvoker, ResourcePolicy, SQLiteResourceManager
from zyro.runtime.agent_runtime import AgentRuntime
from zyro.security.identity import AuthenticatedPrincipal
from zyro.workflows import (
    StepExecution,
    TriggerKind,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStore,
)


class LocalAssistantProvider:
    """Explicit deterministic local-development model; no external truth is claimed."""

    provider_id = "zyro.local"
    available = True

    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        normalized = request.prompt.strip()
        if "find qualified leads" in normalized.lower():
            content = (
                "I created a controlled lead workflow. Lead discovery integrations are not "
                "configured, so no external search or outreach was performed. Connect an "
                "integration, then review any prepared outreach in Approval Center."
            )
        else:
            content = (
                "I processed this through the local ZYRO Executive. External model providers "
                "are not configured, so this response comes from the explicit local-development "
                f"adapter. Your request was: {normalized}"
            )
        return ModelResult(
            ModelResultStatus.SUCCESS,
            request.request_id,
            request.task_id,
            request.agent_id,
            request.instance_id,
            request.correlation_id,
            self.provider_id,
            model.model_id,
            content=content,
            usage=ModelUsage(max(1, len(request.prompt) // 4), max(1, len(content) // 4)),
        )


class ExecutiveChatHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        result = context.invoke_model(
            context.goal,
            system_instruction=(
                "You are the local ZYRO Executive. Report unsupported integrations honestly; "
                "planning never grants execution authority."
            ),
        )
        if not result.succeeded:
            assert result.error is not None
            return AgentExecution.failure(result.error, model_results=(result,))
        return AgentExecution.success(
            {
                "message": result.content,
                "model_id": result.model_id,
                "provider_id": result.provider_id,
                "local": result.provider_id == "zyro.local",
            },
            model_results=(result,),
        )


class ZyroApplication:
    """One real application boundary over Executive, Workflow, Resources and integrations."""

    def __init__(
        self,
        data_dir: str | Path,
        integrations: IntegrationService,
        gemini: GeminiProvider,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = ApplicationStore(self.data_dir / "application.sqlite")
        self.workflows = WorkflowStore(self.data_dir / "workflows.sqlite")
        self.resources = SQLiteResourceManager(self.data_dir / "resources.sqlite", ResourcePolicy())
        local_model = ModelDefinition(
            "zyro-local-assistant",
            "zyro.local",
            "ZYRO Local Development Assistant",
            frozenset({"conversation", "planning"}),
            frozenset({"text"}),
            32_000,
            ModelComplexity.COMPLEX,
            supports_structured_output=True,
            deployment=ModelDeployment.LOCAL,
            input_cost_per_million=0,
            output_cost_per_million=0,
            typical_latency_ms=20,
        )
        gemini_model = ModelDefinition(
            gemini.model_id,
            gemini.provider_id,
            "Google Gemini 2.5 Flash",
            frozenset({"conversation", "planning"}),
            frozenset({"text"}),
            1_048_576,
            ModelComplexity.COMPLEX,
            supports_tool_calling=True,
            supports_structured_output=True,
            deployment=ModelDeployment.CLOUD,
            typical_latency_ms=1_500,
        )
        router = build_model_router((gemini_model, local_model), (gemini, LocalAssistantProvider()))
        model_invoker = ResourceAwareModelInvoker(router, self.resources)
        agents = AgentRegistry()
        self.executive_agent = AgentDefinition(
            "zyro.executive",
            "ZYRO Executive",
            "1.0.0",
            "Interpret and coordinate local user requests without granting authority",
            "core",
            ("conversation", "planning"),
            ("assistant.respond",),
            risk_class=RiskClass.AUTOMATIC,
            model_requirements=ModelRequirements(
                "executive.conversation",
                frozenset({"conversation"}),
                complexity=ModelComplexity.MEDIUM,
                structured_output_required=False,
            ),
        )
        agents.register(self.executive_agent, ExecutiveChatHandler())
        self.executive = ZyroExecutive(
            AgentRuntime(agents, model_invoker=model_invoker), StructuralRuntimeVerifier()
        )
        self.engine = WorkflowEngine(self.workflows)
        self.engine.register("assistant.respond", self._run_assistant_step)
        self.integrations = integrations
        self.gemini = gemini

    def chat(
        self,
        principal: AuthenticatedPrincipal,
        message: str,
        *,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        clean = message.strip()
        if not clean or len(clean) > 16_384:
            raise ValueError("message must contain between 1 and 16384 characters")
        conversation = conversation_id or str(uuid4())
        request_id = str(uuid4())
        correlation_id = str(uuid4())
        workflow_id = str(uuid4())
        self.store.ensure_conversation(conversation, clean[:80])
        self.store.add_message(
            str(uuid4()),
            conversation,
            "user",
            clean,
            request_id=request_id,
            workflow_id=workflow_id,
            correlation_id=correlation_id,
        )
        definition = WorkflowDefinition(
            workflow_id,
            request_id,
            correlation_id,
            principal.principal_id,
            clean,
            (
                WorkflowStep(
                    "understand-and-respond",
                    "Understand, plan and respond",
                    "assistant.respond",
                    self.executive_agent.agent_id,
                    max_attempts=2,
                ),
            ),
            TriggerKind.IMMEDIATE,
            created_at=datetime.now(UTC),
        )
        self.workflows.create(definition)
        completed = self.engine.run(workflow_id)
        tasks = [item for item in self.store.tasks() if item.get("workflow_id") == workflow_id]
        if not tasks:
            raise RuntimeError("workflow completed without a canonical Task projection")
        task = tasks[0]
        response_body = "ZYRO could not produce a response."
        if isinstance(task["result"], dict) and task["result"].get("message"):
            response_body = str(task["result"]["message"])
        elif isinstance(task.get("error"), dict) and task["error"].get("message"):
            response_body = str(task["error"]["message"])
        self.store.add_message(
            str(uuid4()),
            conversation,
            "assistant",
            response_body,
            request_id=request_id,
            task_id=task["task_id"],
            workflow_id=workflow_id,
            correlation_id=correlation_id,
        )
        return {
            "conversation_id": conversation,
            "message": response_body,
            "task": task,
            "workflow": self.workflow_document(completed),
        }

    def _run_assistant_step(self, workflow: Any, step: WorkflowStep) -> StepExecution:
        result = self.executive.handle(
            UserRequest(
                workflow.definition.goal,
                workflow.definition.owner_principal_id,
                step.agent_id,
                request_id=workflow.definition.request_id,
                correlation_id=workflow.definition.correlation_id,
                max_attempts=step.max_attempts,
                workflow_id=workflow.definition.workflow_id,
            )
        )
        value = result.result if isinstance(result.result, dict) else {"value": result.result}
        self.store.record_task(
            {
                "task_id": result.task_id,
                "request_id": result.request_id,
                "workflow_id": workflow.definition.workflow_id,
                "correlation_id": result.correlation_id,
                "goal": workflow.definition.goal,
                "status": result.task_status.value,
                "agent_id": step.agent_id,
                "model_id": value.get("model_id"),
                "tools": [],
                "resource": {"task_limit": self.resources.policy.task_token_limit},
                "verification": {
                    "outcome": result.verification.status.value,
                    "verification_id": result.verification.verification_id,
                    "verifier_id": result.verification.verifier_id,
                    "scope": result.verification.scope,
                },
                "recovery": {},
                "error": None if result.error is None else asdict(result.error),
                "result": value,
                "attempts": result.attempts,
            }
        )
        if result.outcome.value == "VERIFIED_SUCCESS":
            return StepExecution(True, value)
        return StepExecution(
            False,
            error_code=None if result.error is None else result.error.code,
            retryable=False,
        )

    @staticmethod
    def workflow_document(item: Any) -> dict[str, Any]:
        return {
            "workflow_id": item.definition.workflow_id,
            "goal": item.definition.goal,
            "status": item.status.value,
            "revision": item.revision,
            "trigger": item.definition.trigger.value,
            "step_statuses": {key: value.value for key, value in item.step_statuses.items()},
            "step_attempts": item.step_attempts,
            "waiting_reason": item.waiting_reason,
            "error_code": item.error_code,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
            "history": item.history,
        }

    def status(self) -> dict[str, Any]:
        workflows = self.workflows.list()
        tasks = self.store.tasks()
        return {
            "health": "healthy",
            "runtime": "local",
            "model_providers": [
                self.gemini.status(),
                {
                    "provider_id": "zyro.local",
                    "display_name": "ZYRO Local Development Assistant",
                    "model_id": "zyro-local-assistant",
                    "status": "SIMULATED",
                    "configured": True,
                    "available": True,
                    "capabilities": ["conversation", "planning"],
                },
            ],
            "voice": {"status": "BROWSER_DEPENDENT", "provider": "Web Speech API"},
            "browser": {"status": "UNAVAILABLE"},
            "running_work": sum(item.status is WorkflowStatus.RUNNING for item in workflows),
            "task_count": len(tasks),
            "workflow_count": len(workflows),
            "connections": len(self.integrations.connections()),
        }

    def agents(self) -> tuple[dict[str, Any], ...]:
        tasks = self.store.tasks()
        executive_work = [
            item for item in tasks if item.get("agent_id") == self.executive_agent.agent_id
        ]
        current = next(
            (
                item
                for item in executive_work
                if item.get("status") not in {"DONE", "FAILED", "CANCELLED"}
            ),
            None,
        )
        return (
            {
                "agent_id": self.executive_agent.agent_id,
                "name": self.executive_agent.name,
                "role": self.executive_agent.role,
                "domain": self.executive_agent.domain,
                "capabilities": self.executive_agent.capabilities,
                "permissions": self.executive_agent.permissions,
                "model_requirements": {
                    "task_type": (
                        None
                        if self.executive_agent.model_requirements is None
                        else self.executive_agent.model_requirements.task_type
                    )
                },
                "status": "ACTIVE" if current is not None else "AVAILABLE",
                "current_task": None if current is None else current["task_id"],
                "verification_requirements": ("independent structural verification",),
                "recent_work": tuple(
                    {"task_id": item["task_id"], "goal": item["goal"], "status": item["status"]}
                    for item in executive_work[:5]
                ),
            },
            {
                "agent_id": "freelancing.domain",
                "name": "Freelancing Domain",
                "role": "Controlled qualification, outreach and delivery",
                "domain": "freelancing",
                "capabilities": ("qualify", "prepare outreach", "delivery", "qa"),
                "permissions": ("send_outreach requires explicit approval",),
                "model_requirements": {"task_type": "freelancing.controlled-operation"},
                "status": "AVAILABLE",
                "current_task": None,
                "verification_requirements": (
                    "signed outreach evidence",
                    "signed deliverable evidence",
                    "signed QA evidence",
                ),
                "recent_work": (),
            },
        )

    def close(self) -> None:
        self.store.close()
        self.workflows.close()
        self.resources.close()


__all__ = ["LocalAssistantProvider", "ZyroApplication"]
