"""Contracts for the Content Department."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from zyro.core.data import validate_text


@dataclass(frozen=True, slots=True)
class ContentBrief:
    topic: str
    angle: str
    target_audience: str
    key_takeaways: tuple[str, ...]
    tone: str

    def __post_init__(self) -> None:
        for name in ("topic", "angle", "target_audience", "tone"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        object.__setattr__(
            self,
            "key_takeaways",
            tuple(validate_text(item, "takeaway") for item in self.key_takeaways),
        )


@dataclass(frozen=True, slots=True)
class ScriptSegment:
    section_name: str
    spoken_audio: str
    visual_cue: str
    estimated_seconds: int

    def __post_init__(self) -> None:
        for name in ("section_name", "spoken_audio", "visual_cue"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        if self.estimated_seconds < 1:
            raise ValueError("estimated_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ContentScript:
    title: str
    segments: tuple[ScriptSegment, ...]
    total_estimated_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "segments": [
                {
                    "section_name": s.section_name,
                    "spoken_audio": s.spoken_audio,
                    "visual_cue": s.visual_cue,
                    "estimated_seconds": s.estimated_seconds,
                }
                for s in self.segments
            ],
            "total_estimated_seconds": self.total_estimated_seconds,
        }


@dataclass(frozen=True, slots=True)
class PlatformPost:
    platform: str
    text: str
    hashtags: tuple[str, ...] = ()
    media_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("platform", "text"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class PublishingReceipt:
    receipt_id: str
    platform: str
    post_id: str
    post_url: str
    published_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "platform": self.platform,
            "post_id": self.post_id,
            "post_url": self.post_url,
            "published_at": self.published_at.astimezone(UTC).isoformat(),
        }


__all__ = [
    "ContentBrief",
    "ContentScript",
    "PlatformPost",
    "PublishingReceipt",
    "ScriptSegment",
]
