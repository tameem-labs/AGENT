"""Freelancing lead, evidence, qualification, scoring, and pipeline contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class FreelancingContractError(ValueError):
    """Raised when a freelancing domain contract is malformed."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def clean(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FreelancingContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _reject_sensitive(value: Any, path: str = "lead data") -> None:
    forbidden = {
        "secret",
        "password",
        "credential",
        "api_key",
        "access_token",
        "private_key",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.lower().replace("-", "_") in forbidden:
                raise FreelancingContractError(f"{path} cannot contain secret fields")
            _reject_sensitive(item, path)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive(item, path)


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


class LeadState(StrEnum):
    LEAD_FOUND = "LEAD_FOUND"
    VALIDATED = "VALIDATED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    LEAD_QUALIFIED = "LEAD_QUALIFIED"


class ValidationOutcome(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


class QualificationOutcome(StrEnum):
    QUALIFIED = "QUALIFIED"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
    INVALID = "INVALID"


class ScoringOutcome(StrEnum):
    SCORED = "SCORED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    INVALID = "INVALID"


class EvidenceResult(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class SourceReference:
    reference_id: str
    source_type: str
    reference: str
    valid: bool = True

    def __post_init__(self) -> None:
        for field_name in ("reference_id", "source_type", "reference"):
            object.__setattr__(self, field_name, clean(getattr(self, field_name), field_name))


@dataclass(frozen=True, slots=True)
class ResearchEvidence:
    evidence_id: str
    field_name: str
    observed_value: Any
    source: SourceReference
    uncertainty: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", clean(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "field_name", clean(self.field_name, "field_name"))
        if not 0.0 <= self.uncertainty <= 1.0:
            raise FreelancingContractError("uncertainty must be between 0 and 1")
        _reject_sensitive({self.field_name: self.observed_value}, "research evidence")
        object.__setattr__(self, "observed_value", freeze(self.observed_value))


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    check_id: str
    result: EvidenceResult
    summary: str
    source_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", clean(self.check_id, "check_id"))
        object.__setattr__(self, "summary", clean(self.summary, "summary"))
        if any(not value.strip() for value in self.source_references):
            raise FreelancingContractError("source references cannot be empty")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    lead_id: str
    lead_revision: int
    outcome: ValidationOutcome
    policy_version: str
    evidence: tuple[ValidationEvidence, ...]
    missing_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "lead_id", clean(self.lead_id, "lead_id"))
        object.__setattr__(self, "policy_version", clean(self.policy_version, "policy_version"))
        if self.lead_revision < 0 or not self.evidence:
            raise FreelancingContractError("validation requires a revision and evidence")


@dataclass(frozen=True, slots=True)
class CriterionEvidence:
    criterion_id: str
    field_name: str
    observed_value: Any
    expected_condition: str
    result: EvidenceResult
    source_references: tuple[str, ...]
    uncertainty: float | None = None

    def __post_init__(self) -> None:
        for field_name in ("criterion_id", "field_name", "expected_condition"):
            object.__setattr__(self, field_name, clean(getattr(self, field_name), field_name))
        if self.uncertainty is not None and not 0.0 <= self.uncertainty <= 1.0:
            raise FreelancingContractError("uncertainty must be between 0 and 1")
        object.__setattr__(self, "observed_value", freeze(self.observed_value))


@dataclass(frozen=True, slots=True)
class QualificationResult:
    lead_id: str
    lead_revision: int
    outcome: QualificationOutcome
    policy_version: str
    criteria: tuple[CriterionEvidence, ...]
    missing_information: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "lead_id", clean(self.lead_id, "lead_id"))
        object.__setattr__(self, "policy_version", clean(self.policy_version, "policy_version"))
        if self.lead_revision < 0:
            raise FreelancingContractError("lead_revision cannot be negative")


@dataclass(frozen=True, slots=True)
class FactorContribution:
    factor_id: str
    result: EvidenceResult
    weight: int
    contribution: int
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", clean(self.factor_id, "factor_id"))
        if self.weight < 0 or self.contribution not in {0, self.weight}:
            raise FreelancingContractError(
                "factor contribution must be zero or its non-negative weight"
            )


@dataclass(frozen=True, slots=True)
class ScoringResult:
    lead_id: str
    lead_revision: int
    outcome: ScoringOutcome
    policy_version: str
    total_score: int
    factors: tuple[FactorContribution, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "lead_id", clean(self.lead_id, "lead_id"))
        object.__setattr__(self, "policy_version", clean(self.policy_version, "policy_version"))
        if self.lead_revision < 0 or self.total_score < 0:
            raise FreelancingContractError("revision and score cannot be negative")
        if self.total_score != sum(item.contribution for item in self.factors):
            raise FreelancingContractError("total score must equal factor contributions")


@dataclass(frozen=True, slots=True)
class LeadProvenance:
    stage: str
    task_id: str
    verification_id: str
    policy_version: str
    input_revision: int

    def __post_init__(self) -> None:
        for field_name in ("stage", "task_id", "verification_id", "policy_version"):
            object.__setattr__(self, field_name, clean(getattr(self, field_name), field_name))
        if self.input_revision < 0:
            raise FreelancingContractError("input revision cannot be negative")


@dataclass(frozen=True, slots=True)
class LeadRecord:
    lead_id: str
    canonical_key: str
    fields: Mapping[str, Any]
    research_evidence: tuple[ResearchEvidence, ...]
    state: LeadState = LeadState.LEAD_FOUND
    revision: int = 0
    validation: ValidationResult | None = None
    qualification: QualificationResult | None = None
    scoring: ScoringResult | None = None
    provenance: tuple[LeadProvenance, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "lead_id", clean(self.lead_id, "lead_id"))
        object.__setattr__(self, "canonical_key", clean(self.canonical_key, "canonical_key"))
        if self.revision < 0:
            raise FreelancingContractError("lead revision cannot be negative")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise FreelancingContractError("lead timestamps must be timezone-aware")
        _reject_sensitive(self.fields)
        object.__setattr__(self, "fields", freeze(self.fields))
        object.__setattr__(self, "research_evidence", tuple(self.research_evidence))
        object.__setattr__(self, "provenance", tuple(self.provenance))


class PipelineOutcome(StrEnum):
    LEAD_QUALIFIED = "LEAD_QUALIFIED"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    outcome: PipelineOutcome
    lead: LeadRecord
    task_ids: tuple[str, ...] = ()
    event_id: str | None = None
    reason: str | None = None


__all__ = [
    "CriterionEvidence",
    "EvidenceResult",
    "FactorContribution",
    "FreelancingContractError",
    "LeadProvenance",
    "LeadRecord",
    "LeadState",
    "PipelineOutcome",
    "PipelineResult",
    "QualificationOutcome",
    "QualificationResult",
    "ResearchEvidence",
    "ScoringOutcome",
    "ScoringResult",
    "SourceReference",
    "ValidationEvidence",
    "ValidationOutcome",
    "ValidationResult",
    "clean",
    "freeze",
    "utc_now",
]
