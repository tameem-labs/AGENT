"""Unit tests for the Content Department tools and handlers."""

from __future__ import annotations

from zyro.agents.registry import AgentRegistry
from zyro.domains.content import (
    ContentBriefGeneratorHandler,
    ContentPublishingHandler,
    ContentScriptGeneratorHandler,
    PlatformAdaptationHandler,
    register_content_agents,
)
from zyro.tools.contracts import ToolExecutionContext


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("content.tool", "req-1", "task-1", "agent-1", "inst-1", "corr-1")


def test_content_brief_generator() -> None:
    handler = ContentBriefGeneratorHandler()
    res = handler.execute(_ctx(), {"topic": "Agentic AI"})
    assert res.succeeded
    assert res.output is not None
    assert res.output["topic"] == "Agentic AI"
    assert "angle" in res.output
    assert len(res.output["key_takeaways"]) > 0


def test_content_script_generator() -> None:
    handler = ContentScriptGeneratorHandler()
    res = handler.execute(_ctx(), {"title": "Verifiable Execution in Practice"})
    assert res.succeeded
    assert res.output is not None
    assert res.output["title"] == "Verifiable Execution in Practice"
    assert len(res.output["segments"]) == 4
    assert res.output["total_estimated_seconds"] == 75


def test_platform_adaptation() -> None:
    handler = PlatformAdaptationHandler()
    
    # X / Twitter
    res_x = handler.execute(_ctx(), {"platform": "x", "topic": "AI Safety"})
    assert res_x.succeeded
    assert res_x.output is not None
    assert res_x.output["platform"] == "x"
    assert "#SoftwareEngineering" in res_x.output["hashtags"]

    # LinkedIn
    res_li = handler.execute(_ctx(), {"platform": "linkedin", "topic": "AI Architecture"})
    assert res_li.succeeded
    assert res_li.output is not None
    assert "execution != verification" in res_li.output["text"]

    # Instagram
    res_ig = handler.execute(_ctx(), {"platform": "instagram", "topic": "AI Safety"})
    assert res_ig.succeeded
    assert res_ig.output is not None
    assert "#DevLife" in res_ig.output["hashtags"]


def test_content_publishing() -> None:
    handler = ContentPublishingHandler()

    # Missing fields
    err = handler.execute(_ctx(), {"platform": "", "text": ""})
    assert not err.succeeded
    assert err.error is not None
    assert err.error.code == "invalid_request"

    # Valid publishing
    res = handler.execute(_ctx(), {"platform": "instagram", "text": "New breakthrough post"})
    assert res.succeeded
    assert res.output is not None
    assert res.output["platform"] == "instagram"
    assert "receipt_id" in res.output
    assert "instagram.com" in res.output["post_url"]


def test_register_content_agents() -> None:
    reg = AgentRegistry()
    register_content_agents(reg)
    assert reg.get("content.trend") is not None
    assert reg.get("content.script") is not None
    assert reg.get("content.adaptation") is not None
    assert reg.get("content.publishing") is not None
