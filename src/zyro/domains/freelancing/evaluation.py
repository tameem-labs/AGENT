"""Pure deterministic freelancing validation, qualification, and scoring evaluation."""

from __future__ import annotations

from typing import Any

from zyro.domains.freelancing.contracts import (
    CriterionEvidence,
    EvidenceResult,
    FactorContribution,
    LeadRecord,
    LeadState,
    QualificationOutcome,
    QualificationResult,
    ResearchEvidence,
    ScoringOutcome,
    ScoringResult,
    ValidationEvidence,
    ValidationOutcome,
    ValidationResult,
)
from zyro.domains.freelancing.policies import (
    CriterionOperator,
    Predicate,
    QualificationPolicy,
    ScoringPolicy,
    ValidationPolicy,
)


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _evidence_for(lead: LeadRecord, field_name: str) -> tuple[ResearchEvidence, ...]:
    return tuple(item for item in lead.research_evidence if item.field_name == field_name)


def _conflicting_fields(lead: LeadRecord) -> tuple[str, ...]:
    conflicts: set[str] = set()
    by_field: dict[str, list[Any]] = {}
    for item in lead.research_evidence:
        by_field.setdefault(item.field_name, []).append(item.observed_value)
        if item.field_name in lead.fields and lead.fields[item.field_name] != item.observed_value:
            conflicts.add(item.field_name)
    for field_name, values in by_field.items():
        if values and any(value != values[0] for value in values[1:]):
            conflicts.add(field_name)
    return tuple(sorted(conflicts))


class LeadValidator:
    def evaluate(self, lead: LeadRecord, policy: ValidationPolicy) -> ValidationResult:
        checks: list[ValidationEvidence] = []
        invalid = False
        if lead.state not in policy.allowed_states:
            invalid = True
            checks.append(
                ValidationEvidence(
                    "lead_state",
                    EvidenceResult.INVALID,
                    f"Lead state {lead.state.value} is not valid for intake validation.",
                )
            )
        else:
            checks.append(
                ValidationEvidence(
                    "lead_state",
                    EvidenceResult.SATISFIED,
                    f"Lead state {lead.state.value} is accepted.",
                )
            )

        missing_fields = tuple(
            field_name
            for field_name in policy.required_fields
            if field_name not in lead.fields or _missing(lead.fields[field_name])
        )
        checks.append(
            ValidationEvidence(
                "required_fields",
                EvidenceResult.MISSING if missing_fields else EvidenceResult.SATISFIED,
                (
                    f"Missing required fields: {', '.join(missing_fields)}."
                    if missing_fields
                    else "All validation-required fields are present."
                ),
            )
        )

        if policy.require_research_evidence and not lead.research_evidence:
            checks.append(
                ValidationEvidence(
                    "research_evidence",
                    EvidenceResult.MISSING,
                    "Research evidence is required but absent.",
                )
            )
        else:
            checks.append(
                ValidationEvidence(
                    "research_evidence",
                    EvidenceResult.SATISFIED,
                    "Research evidence requirement is satisfied.",
                    tuple(item.source.reference_id for item in lead.research_evidence),
                )
            )

        invalid_sources = tuple(
            item.source.reference_id
            for item in lead.research_evidence
            if not item.source.valid
            or (
                policy.allowed_source_types
                and item.source.source_type not in policy.allowed_source_types
            )
        )
        if invalid_sources:
            invalid = True
        checks.append(
            ValidationEvidence(
                "source_references",
                EvidenceResult.INVALID if invalid_sources else EvidenceResult.SATISFIED,
                (
                    f"Invalid source references: {', '.join(invalid_sources)}."
                    if invalid_sources
                    else "All source references are valid for this policy."
                ),
                invalid_sources,
            )
        )

        conflicts = _conflicting_fields(lead)
        if conflicts:
            invalid = True
        checks.append(
            ValidationEvidence(
                "evidence_consistency",
                EvidenceResult.INVALID if conflicts else EvidenceResult.SATISFIED,
                (
                    f"Conflicting evidence fields: {', '.join(conflicts)}."
                    if conflicts
                    else "No conflicting evidence was found."
                ),
            )
        )

        if invalid:
            outcome = ValidationOutcome.INVALID
        elif missing_fields or (policy.require_research_evidence and not lead.research_evidence):
            outcome = ValidationOutcome.INSUFFICIENT_INFORMATION
        else:
            outcome = ValidationOutcome.VALID
        return ValidationResult(
            lead_id=lead.lead_id,
            lead_revision=lead.revision,
            outcome=outcome,
            policy_version=policy.version,
            evidence=tuple(checks),
            missing_fields=missing_fields,
            conflicts=conflicts,
        )


def _evaluate_predicate(value: Any, predicate: Predicate) -> EvidenceResult:
    if _missing(value):
        return EvidenceResult.MISSING
    if predicate.operator is CriterionOperator.PRESENT:
        return EvidenceResult.SATISFIED
    if predicate.operator is CriterionOperator.EQUALS:
        return (
            EvidenceResult.SATISFIED
            if value == predicate.expected
            else EvidenceResult.NOT_SATISFIED
        )
    if predicate.operator in {CriterionOperator.MINIMUM, CriterionOperator.MAXIMUM}:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isinstance(predicate.expected, (int, float))
            or isinstance(predicate.expected, bool)
        ):
            return EvidenceResult.INVALID
        if predicate.operator is CriterionOperator.MINIMUM:
            return (
                EvidenceResult.SATISFIED
                if value >= predicate.expected
                else EvidenceResult.NOT_SATISFIED
            )
        return (
            EvidenceResult.SATISFIED
            if value <= predicate.expected
            else EvidenceResult.NOT_SATISFIED
        )
    if predicate.operator is CriterionOperator.ALLOWED:
        return (
            EvidenceResult.SATISFIED
            if value in predicate.expected
            else EvidenceResult.NOT_SATISFIED
        )
    return EvidenceResult.INVALID


def _criterion_evidence(lead: LeadRecord, predicate: Predicate) -> CriterionEvidence:
    relevant = _evidence_for(lead, predicate.field_name)
    usable = tuple(
        item
        for item in relevant
        if item.source.valid
        and (
            not predicate.allowed_source_types
            or item.source.source_type in predicate.allowed_source_types
        )
    )
    value = lead.fields.get(predicate.field_name)
    result = _evaluate_predicate(value, predicate)
    if predicate.evidence_required and not usable:
        result = EvidenceResult.MISSING
    uncertainty = max((item.uncertainty for item in usable), default=None)
    return CriterionEvidence(
        criterion_id=predicate.predicate_id,
        field_name=predicate.field_name,
        observed_value=value,
        expected_condition=predicate.expected_description,
        result=result,
        source_references=tuple(item.source.reference_id for item in usable),
        uncertainty=uncertainty,
    )


class QualificationEvaluator:
    def evaluate(self, lead: LeadRecord, policy: QualificationPolicy) -> QualificationResult:
        if (
            lead.state is not LeadState.VALIDATED
            or lead.validation is None
            or lead.validation.outcome is not ValidationOutcome.VALID
        ):
            outcome = (
                QualificationOutcome.INSUFFICIENT_INFORMATION
                if lead.validation is not None
                and lead.validation.outcome is ValidationOutcome.INSUFFICIENT_INFORMATION
                else QualificationOutcome.INVALID
            )
            return QualificationResult(
                lead.lead_id,
                lead.revision,
                outcome,
                policy.version,
                (),
                ("validated_lead",),
            )

        criteria = tuple(_criterion_evidence(lead, item) for item in policy.criteria)
        missing = tuple(
            item.criterion_id for item in criteria if item.result is EvidenceResult.MISSING
        )
        if any(item.result is EvidenceResult.INVALID for item in criteria):
            outcome = QualificationOutcome.INVALID
        elif missing:
            outcome = QualificationOutcome.INSUFFICIENT_INFORMATION
        elif any(item.result is EvidenceResult.NOT_SATISFIED for item in criteria):
            outcome = QualificationOutcome.NOT_QUALIFIED
        else:
            outcome = QualificationOutcome.QUALIFIED
        return QualificationResult(
            lead.lead_id,
            lead.revision,
            outcome,
            policy.version,
            criteria,
            missing,
        )


class ScoringEvaluator:
    def evaluate(self, lead: LeadRecord, policy: ScoringPolicy) -> ScoringResult:
        if (
            lead.state is not LeadState.VALIDATED
            or lead.qualification is None
            or lead.qualification.outcome is not QualificationOutcome.QUALIFIED
        ):
            return ScoringResult(
                lead.lead_id,
                lead.revision,
                ScoringOutcome.NOT_ELIGIBLE,
                policy.version,
                0,
                (),
            )
        contributions: list[FactorContribution] = []
        invalid = False
        for factor in policy.factors:
            evidence = _criterion_evidence(lead, factor.predicate)
            if evidence.result is EvidenceResult.INVALID:
                invalid = True
            contribution = factor.weight if evidence.result is EvidenceResult.SATISFIED else 0
            contributions.append(
                FactorContribution(
                    factor.factor_id,
                    evidence.result,
                    factor.weight,
                    contribution,
                    evidence.source_references,
                )
            )
        outcome = ScoringOutcome.INVALID if invalid else ScoringOutcome.SCORED
        return ScoringResult(
            lead.lead_id,
            lead.revision,
            outcome,
            policy.version,
            sum(item.contribution for item in contributions),
            tuple(contributions),
        )


__all__ = ["LeadValidator", "QualificationEvaluator", "ScoringEvaluator"]
