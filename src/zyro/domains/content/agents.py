"""Agent handlers for the Content Department."""

from __future__ import annotations

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.risk import RiskClass
from zyro.models.contracts import ModelComplexity, ModelRequirements


class TrendResearchAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool("content.generate_brief", {"topic": context.goal})
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        summary = (
            f"Trend brief generated for '{output.get('topic')}'. "
            f"Angle: {output.get('angle')}. Audience: {output.get('target_audience')}."
        )
        return AgentExecution.success(
            {"message": summary, "brief": dict(output)},
            tool_results=(tool_res,),
        )


class ContentScriptAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool("content.generate_script", {"title": context.goal})
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        segments = output.get("segments", [])
        dur = output.get("total_estimated_seconds")
        summary = f"Script created with {len(segments)} segments. Estimated runtime: {dur}s."
        return AgentExecution.success(
            {"message": summary, "script": dict(output)},
            tool_results=(tool_res,),
        )


class PlatformAdaptationAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool(
            "content.adapt_platform", {"topic": context.goal, "platform": "x"}
        )
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        summary = f"Adapted content for {output.get('platform')}: {output.get('text')}"
        return AgentExecution.success(
            {"message": summary, "adaptation": dict(output)},
            tool_results=(tool_res,),
        )


class ContentPublishingAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool(
            "content.publish_post",
            {"platform": "instagram", "text": context.goal},
        )
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        platform = output.get("platform")
        receipt_id = output.get("receipt_id")
        summary = f"Post published to {platform}. Receipt ID: {receipt_id}."
        return AgentExecution.success(
            {"message": summary, "receipt": dict(output)},
            tool_results=(tool_res,),
        )


def register_content_agents(registry: AgentRegistry) -> None:
    trend_def = AgentDefinition(
        "content.trend",
        "Content Trend & Ideation Agent",
        "1.0.0",
        "Identify high-impact themes, angles, and research briefs",
        "content",
        ("trend research", "brief generation"),
        ("content.research",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(trend_def, TrendResearchAgentHandler())

    script_def = AgentDefinition(
        "content.script",
        "Content Script & Copywriting Agent",
        "1.0.0",
        "Write structured scripts, outlines, and core narrative pieces",
        "content",
        ("scriptwriting", "storytelling"),
        ("content.script",),
        risk_class=RiskClass.AUTOMATIC,
        model_requirements=ModelRequirements(
            "content.script",
            frozenset({"conversation", "planning"}),
            complexity=ModelComplexity.COMPLEX,
        ),
    )
    registry.register(script_def, ContentScriptAgentHandler())

    adaptation_def = AgentDefinition(
        "content.adaptation",
        "Platform Adaptation Agent",
        "1.0.0",
        "Format content for multi-platform distribution (X, LinkedIn, Instagram)",
        "content",
        ("adaptation", "copy formatting"),
        ("content.adapt",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(adaptation_def, PlatformAdaptationAgentHandler())

    publishing_def = AgentDefinition(
        "content.publishing",
        "Content Publishing Agent",
        "1.0.0",
        "Dispatch approved content to authorized external channels",
        "content",
        ("publishing", "distribution"),
        ("content.publish",),
        risk_class=RiskClass.STRICT_AUTHORIZATION,
    )
    registry.register(publishing_def, ContentPublishingAgentHandler())


__all__ = [
    "ContentPublishingAgentHandler",
    "ContentScriptAgentHandler",
    "PlatformAdaptationAgentHandler",
    "TrendResearchAgentHandler",
    "register_content_agents",
]
