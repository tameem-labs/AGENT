"""Agent handlers for the Coding Department."""

from __future__ import annotations

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.risk import RiskClass
from zyro.models.contracts import ModelComplexity, ModelRequirements


class CodingDiscoveryAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool("coding.project_discovery", {})
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        summary = (
            f"Discovered project '{output.get('name')}' at '{output.get('project_path')}'. "
            f"Language: {output.get('language')}. Git repository: {output.get('has_git')}."
        )
        return AgentExecution.success(
            {"message": summary, "project": dict(output)},
            tool_results=(tool_res,),
        )


class CodeAnalysisAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool(
            "coding.ast_analysis", {"file_path": "src/zyro/application/service.py"}
        )
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        symbols = output.get("symbols", [])
        modules_count = len(output.get("import_modules", []))
        summary = (
            f"Code analysis completed: syntax valid={output.get('syntax_valid')}. "
            f"Found {len(symbols)} symbols and {modules_count} imports."
        )
        return AgentExecution.success(
            {"message": summary, "analysis": dict(output)},
            tool_results=(tool_res,),
        )


class TestingAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool(
            "coding.run_tests",
            {"command": "pytest", "args": ["tests/unit/test_brain_planner.py", "-q"]},
        )
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        passed = output.get("passed", False)
        status_msg = "PASSED" if passed else "FAILED"
        dur = output.get("duration_ms")
        code = output.get("exit_code")
        summary = f"Test execution {status_msg} in {dur}ms. Exit code: {code}."
        return AgentExecution.success(
            {"message": summary, "test_results": dict(output)},
            tool_results=(tool_res,),
        )


class CodeReviewAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        sys_prompt = "You are the senior code review agent. Diagnose bugs and propose fixes."
        model_res = context.invoke_model(
            f"Perform a code review and diagnosis synthesis based on: {context.goal}",
            system_instruction=sys_prompt,
        )
        if not model_res.succeeded:
            assert model_res.error is not None
            return AgentExecution.failure(model_res.error, model_results=(model_res,))
        return AgentExecution.success(
            {"message": model_res.content, "review_status": "COMPLETED"},
            model_results=(model_res,),
        )


def register_coding_agents(registry: AgentRegistry) -> None:
    discovery_def = AgentDefinition(
        "coding.discovery",
        "Coding Project Discovery Agent",
        "1.0.0",
        "Inspect and locate local project structure, vcs, and dependencies",
        "coding",
        ("discovery", "project inspection"),
        ("coding.discover",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(discovery_def, CodingDiscoveryAgentHandler())

    analysis_def = AgentDefinition(
        "coding.analysis",
        "Coding AST & Architecture Analysis Agent",
        "1.0.0",
        "Inspect source AST, identify imports, functions, classes and syntax validity",
        "coding",
        ("ast analysis", "syntax check"),
        ("coding.analyze",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(analysis_def, CodeAnalysisAgentHandler())

    testing_def = AgentDefinition(
        "coding.testing",
        "Coding Testing & Lint Runner Agent",
        "1.0.0",
        "Run sandboxed test suites and linters with strict timeout and environment isolation",
        "coding",
        ("test execution", "linting"),
        ("coding.test",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(testing_def, TestingAgentHandler())

    review_def = AgentDefinition(
        "coding.review",
        "Coding Review & Synthesis Agent",
        "1.0.0",
        "Synthesize test outputs, code analysis and propose verified remediation",
        "coding",
        ("code review", "verification synthesis"),
        ("coding.review",),
        risk_class=RiskClass.AUTOMATIC,
        model_requirements=ModelRequirements(
            "coding.review",
            frozenset({"conversation", "planning"}),
            complexity=ModelComplexity.COMPLEX,
        ),
    )
    registry.register(review_def, CodeReviewAgentHandler())


__all__ = [
    "CodeAnalysisAgentHandler",
    "CodeReviewAgentHandler",
    "CodingDiscoveryAgentHandler",
    "TestingAgentHandler",
    "register_coding_agents",
]
