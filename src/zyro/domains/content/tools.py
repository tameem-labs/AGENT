"""Tools for the Content Department."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from zyro.core.errors import ErrorInfo
from zyro.domains.content.contracts import (
    ContentBrief,
    ContentScript,
    PlatformPost,
    PublishingReceipt,
    ScriptSegment,
)
from zyro.tools.contracts import ToolExecutionContext, ToolHandlerResult


class ContentBriefGeneratorHandler:
    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        topic = str(arguments.get("topic", "AI Technology Trends")).strip()
        brief = ContentBrief(
            topic=topic,
            angle="Practical engineering breakthroughs and verifiable systems",
            target_audience="Software developers, engineers, and tech founders",
            key_takeaways=(
                "Architecture invariants prevent autonomous agent drift",
                "Deterministic verification beats model self-reporting",
                "Scoped permissions ensure production safety",
            ),
            tone="Authoritative, crisp, and insightful",
        )
        return ToolHandlerResult.success(
            {
                "topic": brief.topic,
                "angle": brief.angle,
                "target_audience": brief.target_audience,
                "key_takeaways": list(brief.key_takeaways),
                "tone": brief.tone,
            }
        )


class ContentScriptGeneratorHandler:
    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        title = str(arguments.get("title", "Why Autonomous AI Needs Verifiable Architecture"))
        script = ContentScript(
            title=title,
            segments=(
                ScriptSegment(
                    "Hook",
                    "Most AI agents fail when side effects aren't verified.",
                    "Fast-cut system diagrams",
                    10,
                ),
                ScriptSegment(
                    "Core Problem",
                    "Self-reporting models hallucinate success claims.",
                    "Highlighting red logs",
                    20,
                ),
                ScriptSegment(
                    "Solution",
                    "Separate planning, authority, and execution gates.",
                    "Clean architecture diagrams",
                    30,
                ),
                ScriptSegment(
                    "Takeaway",
                    "Build systems where proof is independently checked.",
                    "Final title card with repo link",
                    15,
                ),
            ),
            total_estimated_seconds=75,
        )
        return ToolHandlerResult.success(script.to_dict())


class PlatformAdaptationHandler:
    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        platform = str(arguments.get("platform", "x")).lower()
        topic = str(arguments.get("topic", "Autonomous AI"))
        hashtags: tuple[str, ...] = ()
        if platform in {"x", "twitter"}:
            clean_tag = topic.replace(" ", "")
            text = (
                f"Why do autonomous AI agents fail in production?\n\n"
                f"Because they self-report. Here is how verifiable gates fix it: 🧵👇 #{clean_tag}"
            )
            hashtags = (f"#{clean_tag}", "#SoftwareEngineering", "#AIArchitecture")
        elif platform == "linkedin":
            text = (
                "In AI agent design, execution != verification.\n\n"
                "Here is how we separate planning, authority, and verification for workloads."
            )
            hashtags = ("#ArtificialIntelligence", "#Engineering", "#Architecture")
        elif platform == "instagram":
            text = (
                "Stop trusting models that verify their own work. ⚡ "
                "Swipe to see the architecture that guarantees safety."
            )
            hashtags = ("#TechInsights", "#DevLife", "#AI")
        else:
            text = f"Latest insights on {topic}."
            hashtags = ()

        post = PlatformPost(platform=platform, text=text, hashtags=hashtags)
        return ToolHandlerResult.success(
            {
                "platform": post.platform,
                "text": post.text,
                "hashtags": list(post.hashtags),
            }
        )


class ContentPublishingHandler:
    """Consequential publishing tool. Strict authorization and approval required."""

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        platform = str(arguments.get("platform", "")).lower()
        content = str(arguments.get("text", "")).strip()
        if not platform or not content:
            return ToolHandlerResult.failure(
                ErrorInfo("invalid_request", "platform and text are required", "ValidationError")
            )

        post_id = f"{platform}-post-{uuid4().hex[:10]}"
        receipt = PublishingReceipt(
            receipt_id=f"receipt-{uuid4().hex[:10]}",
            platform=platform,
            post_id=post_id,
            post_url=f"https://{platform}.com/zyro/status/{post_id}",
            published_at=datetime.now(UTC),
        )
        return ToolHandlerResult.success(receipt.to_dict())


__all__ = [
    "ContentBriefGeneratorHandler",
    "ContentPublishingHandler",
    "ContentScriptGeneratorHandler",
    "PlatformAdaptationHandler",
]
