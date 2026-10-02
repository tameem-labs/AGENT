"""Contracts for the backend Voice and Audio streaming subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from zyro.core.data import validate_text


class VoiceSessionState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True, slots=True)
class AudioChunk:
    pcm_base64: str
    sample_rate: int = 16000
    is_final: bool = False

    def __post_init__(self) -> None:
        if self.sample_rate < 8000:
            raise ValueError("sample_rate must be at least 8000 Hz")


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    confidence: float
    is_final: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", validate_text(self.text, "text"))
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class TTSAudioResponse:
    audio_base64: str
    format: str = "wav"
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class VoiceSession:
    session_id: str
    state: VoiceSessionState
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "updated_at": self.updated_at.astimezone(UTC).isoformat(),
        }


__all__ = [
    "AudioChunk",
    "TTSAudioResponse",
    "TranscriptionResult",
    "VoiceSession",
    "VoiceSessionState",
]
