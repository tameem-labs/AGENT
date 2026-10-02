"""Structured verification outcomes and evidence independent of execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from zyro.core.errors import ErrorInfo


class VerificationOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    evidence_id: str
    evidence_type: str
    summary: str
    source: str
    observed_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("evidence_id", "evidence_type", "summary", "source"):
            value = getattr(self, field_name)
            if not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    outcome: VerificationOutcome
    summary: str
    scope: str | None = None
    error: ErrorInfo | None = None
    verification_id: str | None = None
    task_id: str | None = None
    execution_id: str | None = None
    verifier_id: str | None = None
    verified_at: datetime | None = None
    evidence: tuple[VerificationEvidence, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.outcome is VerificationOutcome.FAILED and self.error is None:
            raise ValueError("failed verification requires a structured error")
        if self.outcome is not VerificationOutcome.FAILED and self.error is not None:
            raise ValueError("only failed verification can contain an error")
        for field_name in (
            "verification_id",
            "task_id",
            "execution_id",
            "verifier_id",
        ):
            value = getattr(self, field_name)
            if value is None or not value.strip():
                raise ValueError(f"{field_name} is required")
        if self.verified_at is None or self.verified_at.tzinfo is None:
            raise ValueError("verified_at must be timezone-aware")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not self.evidence:
            raise ValueError("verification requires structured evidence")


def utc_now() -> datetime:
    return datetime.now(UTC)
