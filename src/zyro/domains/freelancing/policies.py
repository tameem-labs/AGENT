"""Versioned declarative validation, qualification, and scoring policies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from zyro.domains.freelancing.contracts import LeadState, clean, freeze


class CriterionOperator(StrEnum):
    PRESENT = "PRESENT"
    EQUALS = "EQUALS"
    MINIMUM = "MINIMUM"
    MAXIMUM = "MAXIMUM"
    ALLOWED = "ALLOWED"


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    version: str
    required_fields: tuple[str, ...]
    require_research_evidence: bool = True
    allowed_source_types: frozenset[str] = frozenset()
    allowed_states: frozenset[LeadState] = frozenset({LeadState.LEAD_FOUND})

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", clean(self.version, "version"))
        object.__setattr__(
            self,
            "required_fields",
            tuple(clean(item, "required_field") for item in self.required_fields),
        )
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields cannot contain duplicates")
        object.__setattr__(
            self,
            "allowed_source_types",
            frozenset(clean(item, "source_type") for item in self.allowed_source_types),
        )
        if not self.allowed_states:
            raise ValueError("allowed_states cannot be empty")


@dataclass(frozen=True, slots=True)
class Predicate:
    predicate_id: str
    field_name: str
    operator: CriterionOperator
    expected: Any = None
    evidence_required: bool = False
    allowed_source_types: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "predicate_id", clean(self.predicate_id, "predicate_id"))
        object.__setattr__(self, "field_name", clean(self.field_name, "field_name"))
        if self.operator is not CriterionOperator.PRESENT and self.expected is None:
            raise ValueError(f"{self.operator} requires an expected value")
        if self.operator is CriterionOperator.ALLOWED:
            if not isinstance(self.expected, (list, tuple, set, frozenset)) or not self.expected:
                raise ValueError("ALLOWED requires a non-empty collection")
            object.__setattr__(self, "expected", tuple(self.expected))
        else:
            object.__setattr__(self, "expected", freeze(self.expected))
        object.__setattr__(
            self,
            "allowed_source_types",
            frozenset(clean(item, "source_type") for item in self.allowed_source_types),
        )

    @property
    def expected_description(self) -> str:
        if self.operator is CriterionOperator.PRESENT:
            return "value is present"
        return f"{self.operator.value}:{self.expected!r}"


@dataclass(frozen=True, slots=True)
class QualificationPolicy:
    version: str
    criteria: tuple[Predicate, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", clean(self.version, "version"))
        object.__setattr__(self, "criteria", tuple(self.criteria))
        identities = [item.predicate_id for item in self.criteria]
        if not self.criteria or len(set(identities)) != len(identities):
            raise ValueError("qualification criteria must be non-empty and uniquely identified")


@dataclass(frozen=True, slots=True)
class ScoringFactor:
    factor_id: str
    predicate: Predicate
    weight: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", clean(self.factor_id, "factor_id"))
        if self.weight < 0:
            raise ValueError("factor weight cannot be negative")


@dataclass(frozen=True, slots=True)
class ScoringPolicy:
    version: str
    factors: tuple[ScoringFactor, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", clean(self.version, "version"))
        object.__setattr__(self, "factors", tuple(self.factors))
        identities = [item.factor_id for item in self.factors]
        if not self.factors or len(set(identities)) != len(identities):
            raise ValueError("scoring factors must be non-empty and uniquely identified")


__all__ = [
    "CriterionOperator",
    "Predicate",
    "QualificationPolicy",
    "ScoringFactor",
    "ScoringPolicy",
    "ValidationPolicy",
]
