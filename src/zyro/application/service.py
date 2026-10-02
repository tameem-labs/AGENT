"""Canonical application service used by the localhost API and UI."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.application.store import ApplicationStore
from zyro.computer import (
    BrowserNavigateHandler,
    ScreenCaptureHandler,
    WindowsFocusWindowHandler,
    WindowsListWindowsHandler,
    WindowsSandboxedExecHandler,
)
from zyro.context import ContextAssembler, ContextRequest
from zyro.core.executive import UserRequest, ZyroExecutive
from zyro.core.risk import RiskClass
from zyro.core.scope import ResourceScope, ScopeKind
from zyro.domains.coding import (
    ASTAnalysisHandler,
    CodingProjectDiscoveryHandler,
    SandboxedCommandRunnerHandler,
    register_coding_agents,
)
from zyro.domains.content import (
    ContentBriefGeneratorHandler,
    ContentPublishingHandler,
    ContentScriptGeneratorHandler,
    PlatformAdaptationHandler,
    register_content_agents,
)
from zyro.domains.freelancing.discovery import LeadDiscoveryToolHandler
from zyro.domains.operations import (
    DatabaseCheckHandler,
    SystemHealthCheckHandler,
    register_operations_agents,
)
from zyro.domains.personal import (
    PersonalAgendaHandler,
    PersonalReminderHandler,
    register_personal_agents,
)
from zyro.execution.verification import StructuralRuntimeVerifier
from zyro.integrations import EncryptedCredentialStore, IntegrationService
from zyro.integrations.actions import ConnectedAccountActions
from zyro.integrations.writes import register_consequential_write_tools
from zyro.intelligence import AttentionService
from zyro.knowledge import SQLiteKnowledgeStore
from zyro.learning import MemoryConsolidator, WorkflowExperience
from zyro.memory import SQLiteMemoryStore
from zyro.models.anthropic import AnthropicProvider
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
from zyro.models.ollama import OllamaProvider
from zyro.models.openai import OpenAIProvider
from zyro.models.router import build_model_router
from zyro.persistence import DatabaseCatalog
from zyro.planning import BrainPlanner
from zyro.research import BraveSearchHandler, ResearchAgentHandler, SafeSourceReader
from zyro.resources import (
    ResourceAwareModelInvoker,
    ResourceAwareToolInvoker,
    ResourcePolicy,
    SQLiteResourceManager,
)
from zyro.runtime.agent_runtime import AgentRuntime
from zyro.security.approval import ApprovalService
from zyro.security.authorization import ToolAuthorizationService
from zyro.security.identity import AuthenticatedPrincipal
from zyro.security.permission import (
    Permission,
    PermissionEvaluator,
    PermissionScope,
    PermissionStore,
    PrincipalDirectory,
)
from zyro.security.policy import RiskPolicy
from zyro.security.resource_authorization import PermissionResourceAuthorizer
from zyro.state import SQLiteStateStore
from zyro.tools.contracts import ToolDefinition
from zyro.tools.executor import ToolExecutor
from zyro.tools.registry import ToolRegistry
from zyro.voice import VoiceEngine
from zyro.workflows import (
    BackgroundSchedulerWorker,
    LocalWorkflowScheduler,
    StepExecution,
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
        approvals: ApprovalService,
        credentials: EncryptedCredentialStore,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = ApplicationStore(self.data_dir / "application.sqlite")
        self.workflows = WorkflowStore(self.data_dir / "workflows.sqlite")
        self.resources = SQLiteResourceManager(
            self.data_dir / "resources.sqlite", ResourcePolicy()
        )

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
        openai_model = ModelDefinition(
            "gpt-4o",
            "openai",
            "OpenAI GPT-4o",
            frozenset({"conversation", "planning", "coding"}),
            frozenset({"text"}),
            128_000,
            ModelComplexity.COMPLEX,
            supports_tool_calling=True,
            supports_structured_output=True,
            deployment=ModelDeployment.CLOUD,
            typical_latency_ms=1_200,
        )
        anthropic_model = ModelDefinition(
            "claude-3-5-sonnet-20241022",
            "anthropic",
            "Anthropic Claude 3.5 Sonnet",
            frozenset({"conversation", "planning", "coding"}),
            frozenset({"text"}),
            200_000,
            ModelComplexity.COMPLEX,
            supports_tool_calling=True,
            supports_structured_output=True,
            deployment=ModelDeployment.CLOUD,
            typical_latency_ms=1_400,
        )
        ollama_model = ModelDefinition(
            "llama3.1:8b",
            "ollama",
            "Ollama Llama 3.1 8B",
            frozenset({"conversation", "planning", "coding"}),
            frozenset({"text"}),
            8_192,
            ModelComplexity.MEDIUM,
            supports_tool_calling=False,
            supports_structured_output=False,
            deployment=ModelDeployment.LOCAL,
            typical_latency_ms=800,
        )

        self.openai_provider = OpenAIProvider(credentials)
        self.anthropic_provider = AnthropicProvider(credentials)
        self.ollama_provider = OllamaProvider()

        router = build_model_router(
            (gemini_model, openai_model, anthropic_model, ollama_model, local_model),
            (
                gemini,
                self.openai_provider,
                self.anthropic_provider,
                self.ollama_provider,
                LocalAssistantProvider(),
            ),
        )
        model_invoker = ResourceAwareModelInvoker(router, self.resources)

        tool_registry = ToolRegistry()
        tool_registry.register(
            ToolDefinition(
                "research.web_search",
                "Brave Web Search",
                "1",
                "Search the public web through the official Brave Search API",
                frozenset({"research.search"}),
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "required": ["query"],
                },
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "results": {"type": "array"},
                    },
                    "required": ["query", "results"],
                },
                "brave-search",
                RiskClass.AUTOMATIC,
                timeout_seconds=20,
            ),
            BraveSearchHandler(credentials),
        )
        tool_registry.register(
            ToolDefinition(
                "research.read_source",
                "Safe Public Source Reader",
                "1",
                "Read one bounded public HTTP source with SSRF protection",
                frozenset({"research.read"}),
                {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
                {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "text": {"type": "string"},
                        "digest": {"type": "string"},
                        "retrieved_at": {"type": "string"},
                        "method": {"type": "string"},
                    },
                    "required": ["url", "text", "digest", "retrieved_at", "method"],
                },
                "safe-source-reader",
                RiskClass.AUTOMATIC,
                timeout_seconds=20,
            ),
            SafeSourceReader(),
        )

        # Consequential write tools
        self.account_actions = ConnectedAccountActions(integrations)
        register_consequential_write_tools(tool_registry, self.account_actions)

        # Computer and browser tools
        def _reg(
            name: str,
            display: str,
            cap: str,
            handler: Any,
            risk: RiskClass = RiskClass.AUTOMATIC,
        ) -> None:
            tool_registry.register(
                ToolDefinition(
                    name,
                    display,
                    "1.0.0",
                    display,
                    frozenset({cap}),
                    {"type": "object", "properties": {}},
                    {"type": "object", "properties": {}},
                    name,
                    risk,
                    timeout_seconds=20,
                ),
                handler,
            )

        _reg("computer.browse", "Safe Web Browser", "computer.browse", BrowserNavigateHandler())
        _reg(
            "computer.list_windows",
            "List Windows",
            "computer.list_windows",
            WindowsListWindowsHandler(),
        )
        _reg(
            "computer.focus_window",
            "Focus Window",
            "computer.focus_window",
            WindowsFocusWindowHandler(),
        )
        _reg(
            "computer.sandboxed_exec",
            "Sandboxed Exec",
            "computer.sandboxed_exec",
            WindowsSandboxedExecHandler(),
        )
        _reg("screen.capture", "Screen Capture", "screen.capture", ScreenCaptureHandler())

        # Coding tools
        _reg(
            "coding.project_discovery",
            "Project Discovery",
            "coding.discover",
            CodingProjectDiscoveryHandler(self.data_dir),
        )
        _reg("coding.ast_analysis", "AST Analysis", "coding.analyze", ASTAnalysisHandler())
        _reg(
            "coding.sandboxed_exec",
            "Coding Command Runner",
            "coding.sandboxed_exec",
            SandboxedCommandRunnerHandler(self.data_dir),
        )

        # Content tools
        _reg(
            "content.brief_generator",
            "Brief Generator",
            "content.brief",
            ContentBriefGeneratorHandler(),
        )
        _reg(
            "content.script_generator",
            "Script Generator",
            "content.script",
            ContentScriptGeneratorHandler(),
        )
        _reg(
            "content.platform_adapter",
            "Platform Adapter",
            "content.adapt",
            PlatformAdaptationHandler(),
        )
        _reg(
            "content.publisher",
            "Content Publisher",
            "content.publish",
            ContentPublishingHandler(),
            RiskClass.STRICT_AUTHORIZATION,
        )

        # Personal tools
        _reg("personal.list_agenda", "List Agenda", "personal.agenda", PersonalAgendaHandler())
        _reg(
            "personal.create_reminder",
            "Create Reminder",
            "personal.reminder",
            PersonalReminderHandler(),
        )

        # Operations tools
        _reg(
            "operations.database_check",
            "Database Check",
            "operations.database_check",
            DatabaseCheckHandler(self.data_dir),
        )
        _reg(
            "operations.system_health",
            "System Health Check",
            "operations.health_check",
            SystemHealthCheckHandler(),
        )

        # Freelancing tools
        _reg(
            "freelancing.discover_leads",
            "Discover Leads",
            "freelancing.discover",
            LeadDiscoveryToolHandler(),
        )

        principals = PrincipalDirectory()
        for p in (
            "zyro.executive",
            "zyro.research",
            "zyro.coding",
            "zyro.content",
            "zyro.personal",
            "zyro.operations",
            "zyro.freelancing",
            "local-owner",
        ):
            principals.register(p)

        permissions = PermissionStore()
        for capability, tool_id in (
            ("research.search", "research.web_search"),
            ("research.read", "research.read_source"),
        ):
            permissions.add(
                Permission(
                    f"permission-{capability}",
                    "zyro.research",
                    capability,
                    PermissionScope(tool_id=tool_id, target="public-web", action="execute"),
                    "product-v1",
                )
            )

        all_capabilities = {
            "research.search",
            "research.read",
            "assistant.respond",
            "research.execute",
            "coding.discover",
            "coding.analyze",
            "coding.test",
            "coding.review",
            "coding.edit",
            "coding.sandboxed_exec",
            "content.brief",
            "content.script",
            "content.adapt",
            "content.publish",
            "personal.agenda",
            "personal.organize",
            "personal.reminder",
            "personal.remind",
            "operations.database_check",
            "operations.health_check",
            "freelancing.discover",
            "freelancing.qualify",
            "freelancing.prepare_outreach",
            "freelancing.dispatch",
            "google.gmail_send",
            "google.calendar_create",
            "github.create_pr",
            "crm.create_lead",
            "computer.browse",
            "computer.list_windows",
            "computer.focus_window",
            "computer.sandboxed_exec",
            "screen.capture",
            "memory.read",
            "memory.write",
            "memory.read.restricted",
            "knowledge.read",
            "knowledge.write",
            "state.read",
            "state.write",
            "context.assemble",
        }

        seq = 0
        for cap in sorted(all_capabilities):
            for princ in (
                "zyro.executive",
                "zyro.research",
                "zyro.coding",
                "zyro.content",
                "zyro.personal",
                "zyro.operations",
                "zyro.freelancing",
                "local-owner",
            ):
                seq += 1
                permissions.add(
                    Permission(
                        f"perm-{seq}-{princ}-{cap}",
                        princ,
                        cap,
                        PermissionScope(domain="*"),
                        "product-v1",
                    )
                )

        evaluator = PermissionEvaluator(
            permissions,
            principals,
            frozenset(all_capabilities),
            policy_version="product-v1",
        )
        tool_authorizer = ToolAuthorizationService(
            evaluator, RiskPolicy(policy_version="product-v1"), approvals
        )
        tool_invoker = ResourceAwareToolInvoker(
            ToolExecutor(tool_registry, tool_authorizer), self.resources
        )

        resource_authorizer = PermissionResourceAuthorizer(evaluator)
        self.memory = SQLiteMemoryStore(
            self.data_dir / "memory.sqlite", resource_authorizer
        )
        self.knowledge = SQLiteKnowledgeStore(
            self.data_dir / "knowledge.sqlite", resource_authorizer
        )
        self.state_store = SQLiteStateStore(
            self.data_dir / "state.sqlite",
            resource_authorizer,
            {
                "runtime": "zyro.executive",
                "task": "zyro.executive",
                "system": "zyro.operations",
                "personal": "zyro.personal",
                "coding": "zyro.coding",
                "content": "zyro.content",
                "freelancing": "zyro.freelancing",
                "research": "zyro.research",
            },
        )
        self.context_assembler = ContextAssembler(
            self.memory, self.state_store, self.knowledge, resource_authorizer
        )
        self.learning = MemoryConsolidator(self.memory)

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
        self.research_agent = AgentDefinition(
            "zyro.research",
            "ZYRO Research",
            "1.0.0",
            "Collect and compare externally sourced evidence without inventing sources",
            "research",
            ("web search", "source reading", "comparison", "synthesis"),
            ("research.search", "research.read"),
            risk_class=RiskClass.AUTOMATIC,
            model_requirements=ModelRequirements(
                "research.synthesis",
                frozenset({"conversation", "planning"}),
                complexity=ModelComplexity.COMPLEX,
            ),
        )
        agents.register(self.research_agent, ResearchAgentHandler())

        register_coding_agents(agents)
        register_content_agents(agents)
        register_personal_agents(agents)
        register_operations_agents(agents)

        self.agent_registry = agents
        self.executive = ZyroExecutive(
            AgentRuntime(agents, model_invoker=model_invoker, tool_invoker=tool_invoker),
            StructuralRuntimeVerifier(),
        )
        self.planner = BrainPlanner(agents, tool_registry)
        self.engine = WorkflowEngine(self.workflows)

        # Register execution handlers for all workflow capabilities
        for cap in (
            "assistant.respond",
            "research.execute",
            "coding.discover",
            "coding.analyze",
            "coding.test",
            "coding.review",
            "coding.edit",
            "coding.sandboxed_exec",
            "content.brief",
            "content.script",
            "content.adapt",
            "content.publish",
            "personal.agenda",
            "personal.organize",
            "personal.reminder",
            "personal.remind",
            "operations.database_check",
            "operations.health_check",
            "freelancing.discover",
            "freelancing.qualify",
            "freelancing.prepare_outreach",
            "freelancing.dispatch",
        ):
            self.engine.register(cap, self._run_assistant_step)

        self.integrations = integrations
        self.gemini = gemini
        self.voice = VoiceEngine()
        self.attention = AttentionService(approvals, self.workflows)
        self.scheduler = LocalWorkflowScheduler(self.workflows, self.engine)
        self.scheduler_worker = BackgroundSchedulerWorker(self.scheduler)

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

        context_req = ContextRequest(
            task_id=request_id,
            requester_id=principal.principal_id,
            scope=ResourceScope(ScopeKind.USER, principal.principal_id),
            current_instruction=clean,
            query=clean,
        )
        assembled = self.context_assembler.assemble(context_req)
        context_summary = (
            f"{len(assembled.items)} context items assembled" if assembled.items else None
        )

        plan = self.planner.plan(clean, principal.principal_id, context_summary=context_summary)
        definition = self.planner.compile_to_workflow(
            plan, request_id, correlation_id, principal.principal_id, workflow_id=workflow_id
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

        # Long-Term Learning: extract preferences and record workflow experience
        try:
            prefs = self.learning.extract_preferences(clean)
            for pref in prefs:
                self.learning.persist_preference(pref, principal.principal_id)
            self.learning.record_experience(
                WorkflowExperience(
                    workflow_id=workflow_id,
                    intent=plan.intent.value,
                    outcome=completed.status.value,
                    duration_seconds=1.0,
                    step_count=len(plan.steps),
                )
            )
        except Exception:
            pass

        return {
            "conversation_id": conversation,
            "message": response_body,
            "task": task,
            "workflow": self.workflow_document(completed),
            "plan": plan.to_dict(),
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

        tools_used: list[str] = []
        if step.agent_id == self.research_agent.agent_id:
            tools_used = ["research.web_search", "research.read_source"]
        elif step.agent_id.startswith("coding."):
            tools_used = ["coding.project_discovery", "coding.ast_analysis"]
        elif step.agent_id.startswith("content."):
            tools_used = ["content.brief_generator", "content.script_generator"]
        elif step.agent_id.startswith("personal."):
            tools_used = ["personal.list_agenda", "personal.create_reminder"]
        elif step.agent_id.startswith("operations."):
            tools_used = ["operations.database_check"]
        elif step.agent_id.startswith("freelancing."):
            tools_used = ["freelancing.discover_leads"]

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
                "tools": tools_used,
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
        db_catalog = DatabaseCatalog(self.data_dir)
        db_health = db_catalog.check_integrity()
        return {
            "health": "healthy" if all(db_health.values()) else "degraded",
            "runtime": "local",
            "database_integrity": db_health,
            "model_providers": [
                self.gemini.status(),
                self.openai_provider.status(),
                self.anthropic_provider.status(),
                self.ollama_provider.status(),
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
            "voice": self.voice.status(),
            "browser": {"status": "AVAILABLE_SANDBOXED", "engine": "cdp_safe_browser"},
            "running_work": sum(item.status is WorkflowStatus.RUNNING for item in workflows),
            "task_count": len(tasks),
            "workflow_count": len(workflows),
            "connections": len(self.integrations.connections()),
        }

    def agents(self) -> tuple[dict[str, Any], ...]:
        tasks = self.store.tasks()
        registered = self.agent_registry.list()
        out: list[dict[str, Any]] = []

        for agent in registered:
            agent_work = [item for item in tasks if item.get("agent_id") == agent.agent_id]
            current = next(
                (
                    item
                    for item in agent_work
                    if item.get("status") not in {"DONE", "FAILED", "CANCELLED"}
                ),
                None,
            )
            out.append(
                {
                    "agent_id": agent.agent_id,
                    "name": agent.name,
                    "role": agent.role,
                    "domain": agent.domain,
                    "capabilities": agent.capabilities,
                    "permissions": agent.permissions,
                    "model_requirements": {
                        "task_type": (
                            None
                            if agent.model_requirements is None
                            else agent.model_requirements.task_type
                        )
                    },
                    "status": "ACTIVE" if current is not None else "AVAILABLE",
                    "current_task": None if current is None else current["task_id"],
                    "verification_requirements": (
                        "independent structural verification",
                    ),
                    "recent_work": tuple(
                        {
                            "task_id": item["task_id"],
                            "goal": item["goal"],
                            "status": item["status"],
                        }
                        for item in agent_work[:5]
                    ),
                }
            )

        # Include freelancing domain representation
        out.append(
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
            }
        )
        return tuple(out)

    def close(self) -> None:
        self.scheduler_worker.stop_sync()
        self.store.close()
        self.workflows.close()
        self.resources.close()
        self.memory.close()
        self.knowledge.close()
        self.state_store.close()


__all__ = ["LocalAssistantProvider", "ZyroApplication"]
