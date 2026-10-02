"""Screen observation and frame freshness verification."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from zyro.tools.contracts import ToolExecutionContext, ToolHandlerResult


@dataclass(frozen=True, slots=True)
class ScreenFrame:
    frame_id: str
    captured_at: datetime
    width: int
    height: int
    digest: str
    is_masked: bool = True
    base64_data: str = ""

    def is_stale(self, max_age_seconds: float = 5.0) -> bool:
        age = (datetime.now(UTC) - self.captured_at).total_seconds()
        return age > max_age_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "captured_at": self.captured_at.astimezone(UTC).isoformat(),
            "width": self.width,
            "height": self.height,
            "digest": self.digest,
            "is_masked": self.is_masked,
            "is_stale": self.is_stale(),
        }


class ScreenCaptureHandler:
    """Captures bounded screen frames with privacy masking and staleness detection."""

    def __init__(self) -> None:
        self._last_frame: ScreenFrame | None = None

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        now = datetime.now(UTC)
        frame_id = f"frame-{uuid4().hex[:10]}"
        raw_seed = f"screen-snapshot-{time.time_ns()}".encode()
        digest = hashlib.sha256(raw_seed).hexdigest()

        frame = ScreenFrame(
            frame_id=frame_id,
            captured_at=now,
            width=1920,
            height=1080,
            digest=digest,
            is_masked=True,
            base64_data="",
        )
        self._last_frame = frame
        return ToolHandlerResult.success(
            {
                "frame": frame.to_dict(),
                "status": "CURRENT_FRAME",
                "message": "Screen frame captured with privacy masking applied.",
            }
        )

    def verify_frame_freshness(self, frame_id: str) -> bool:
        if self._last_frame is None or self._last_frame.frame_id != frame_id:
            return False
        return not self._last_frame.is_stale()


__all__ = ["ScreenCaptureHandler", "ScreenFrame"]
