"""Authoritative in-process freelancing lead store with compare-and-set revisions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from zyro.domains.freelancing.contracts import (
    LeadProvenance,
    LeadRecord,
    LeadState,
    QualificationOutcome,
    QualificationResult,
    ScoringOutcome,
    ScoringResult,
    ValidationOutcome,
    ValidationResult,
    utc_now,
)


class IntakeOutcome(StrEnum):
    CREATED = "CREATED"
    DUPLICATE = "DUPLICATE"


class StateUpdateOutcome(StrEnum):
    UPDATED = "UPDATED"
    STALE = "STALE"
    INVALID_STATE = "INVALID_STATE"
    NO_CHANGE = "NO_CHANGE"


@dataclass(frozen=True, slots=True)
class IntakeResult:
    outcome: IntakeOutcome
    lead: LeadRecord


@dataclass(frozen=True, slots=True)
class StateUpdateResult:
    outcome: StateUpdateOutcome
    lead: LeadRecord
    reason: str


class LeadRepository(Protocol):
    def ingest(self, lead: LeadRecord) -> IntakeResult: ...

    def get(self, lead_id: str) -> LeadRecord: ...

    def commit_validation(
        self,
        lead_id: str,
        expected_revision: int,
        result: ValidationResult,
        provenance: LeadProvenance,
    ) -> StateUpdateResult: ...

    def commit_qualification(
        self,
        lead_id: str,
        expected_revision: int,
        result: QualificationResult,
        provenance: LeadProvenance,
    ) -> StateUpdateResult: ...

    def commit_scoring(
        self,
        lead_id: str,
        expected_revision: int,
        result: ScoringResult,
        provenance: LeadProvenance,
    ) -> StateUpdateResult: ...


class InProcessLeadStore:
    """First-seen canonical lead store; all writes require an expected revision."""

    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._leads: dict[str, LeadRecord] = {}
        self._canonical: dict[str, str] = {}

    def ingest(self, lead: LeadRecord) -> IntakeResult:
        existing_id = self._canonical.get(lead.canonical_key)
        if existing_id is not None:
            return IntakeResult(IntakeOutcome.DUPLICATE, self._leads[existing_id])
        if lead.lead_id in self._leads:
            return IntakeResult(IntakeOutcome.DUPLICATE, self._leads[lead.lead_id])
        if lead.state is not LeadState.LEAD_FOUND or lead.revision != 0:
            raise ValueError("new leads must enter at LEAD_FOUND revision zero")
        self._leads[lead.lead_id] = lead
        self._canonical[lead.canonical_key] = lead.lead_id
        return IntakeResult(IntakeOutcome.CREATED, lead)

    def get(self, lead_id: str) -> LeadRecord:
        try:
            return self._leads[lead_id]
        except KeyError as error:
            raise KeyError(f"lead is not registered: {lead_id}") from error

    def commit_validation(
        self,
        lead_id: str,
        expected_revision: int,
        result: ValidationResult,
        provenance: LeadProvenance,
    ) -> StateUpdateResult:
        current = self.get(lead_id)
        stale = self._stale(current, expected_revision)
        if stale is not None:
            return stale
        if current.state is not LeadState.LEAD_FOUND or result.lead_revision != expected_revision:
            return StateUpdateResult(
                StateUpdateOutcome.INVALID_STATE,
                current,
                "Validation can only commit against the evaluated LEAD_FOUND revision.",
            )
        state = {
            ValidationOutcome.VALID: LeadState.VALIDATED,
            ValidationOutcome.INVALID: LeadState.REJECTED,
            ValidationOutcome.INSUFFICIENT_INFORMATION: LeadState.PENDING,
        }[result.outcome]
        return self._replace(current, state=state, validation=result, provenance=provenance)

    def commit_qualification(
        self,
        lead_id: str,
        expected_revision: int,
        result: QualificationResult,
        provenance: LeadProvenance,
    ) -> StateUpdateResult:
        current = self.get(lead_id)
        stale = self._stale(current, expected_revision)
        if stale is not None:
            return stale
        if current.state is not LeadState.VALIDATED or result.lead_revision != expected_revision:
            return StateUpdateResult(
                StateUpdateOutcome.INVALID_STATE,
                current,
                "Qualification requires the evaluated authoritative VALIDATED revision.",
            )
        state = {
            QualificationOutcome.QUALIFIED: LeadState.VALIDATED,
            QualificationOutcome.NOT_QUALIFIED: LeadState.REJECTED,
            QualificationOutcome.INSUFFICIENT_INFORMATION: LeadState.PENDING,
            QualificationOutcome.INVALID: LeadState.REJECTED,
        }[result.outcome]
        return self._replace(current, state=state, qualification=result, provenance=provenance)

    def commit_scoring(
        self,
        lead_id: str,
        expected_revision: int,
        result: ScoringResult,
        provenance: LeadProvenance,
    ) -> StateUpdateResult:
        current = self.get(lead_id)
        stale = self._stale(current, expected_revision)
        if stale is not None:
            return stale
        if (
            current.state is not LeadState.VALIDATED
            or current.qualification is None
            or current.qualification.outcome is not QualificationOutcome.QUALIFIED
            or result.outcome is ScoringOutcome.NOT_ELIGIBLE
            or result.lead_revision != expected_revision
        ):
            return StateUpdateResult(
                StateUpdateOutcome.INVALID_STATE,
                current,
                "Scoring requires verified qualification and a reproducible scored revision.",
            )
        state = (
            LeadState.LEAD_QUALIFIED
            if result.outcome is ScoringOutcome.SCORED
            else LeadState.REJECTED
        )
        return self._replace(
            current,
            state=state,
            scoring=result,
            provenance=provenance,
        )

    @staticmethod
    def _stale(current: LeadRecord, expected_revision: int) -> StateUpdateResult | None:
        if current.revision != expected_revision:
            return StateUpdateResult(
                StateUpdateOutcome.STALE,
                current,
                (
                    f"Expected revision {expected_revision}; authoritative revision is "
                    f"{current.revision}."
                ),
            )
        return None

    def _replace(
        self,
        current: LeadRecord,
        *,
        state: LeadState,
        provenance: LeadProvenance,
        validation: ValidationResult | None = None,
        qualification: QualificationResult | None = None,
        scoring: ScoringResult | None = None,
    ) -> StateUpdateResult:
        if any(item.stage == provenance.stage for item in current.provenance):
            return StateUpdateResult(
                StateUpdateOutcome.NO_CHANGE,
                current,
                f"Stage {provenance.stage} was already committed.",
            )
        updated = replace(
            current,
            state=state,
            revision=current.revision + 1,
            validation=validation or current.validation,
            qualification=qualification or current.qualification,
            scoring=scoring or current.scoring,
            provenance=(*current.provenance, provenance),
            updated_at=self._clock(),
        )
        self._leads[current.lead_id] = updated
        return StateUpdateResult(StateUpdateOutcome.UPDATED, updated, "State committed.")


__all__ = [
    "InProcessLeadStore",
    "IntakeOutcome",
    "IntakeResult",
    "LeadRepository",
    "StateUpdateOutcome",
    "StateUpdateResult",
]
