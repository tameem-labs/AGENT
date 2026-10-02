from __future__ import annotations

from dataclasses import replace

import pytest

from zyro.domains.freelancing.contracts import (
    EvidenceResult,
    FreelancingContractError,
    LeadRecord,
    LeadState,
    QualificationOutcome,
    ResearchEvidence,
    ScoringOutcome,
    SourceReference,
    ValidationOutcome,
)
from zyro.domains.freelancing.evaluation import (
    LeadValidator,
    QualificationEvaluator,
    ScoringEvaluator,
)
from zyro.domains.freelancing.policies import (
    CriterionOperator,
    Predicate,
    QualificationPolicy,
    ScoringFactor,
    ScoringPolicy,
    ValidationPolicy,
)


def source(
    reference_id: str = "source-1",
    *,
    source_type: str = "fixture",
    valid: bool = True,
) -> SourceReference:
    return SourceReference(reference_id, source_type, f"fixture://{reference_id}", valid)


def evidence(
    evidence_id: str,
    field_name: str,
    value: object,
    *,
    source_reference: SourceReference | None = None,
) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_id,
        field_name,
        value,
        source_reference or source(evidence_id),
    )


def found_lead(**changes: object) -> LeadRecord:
    values: dict[str, object] = {
        "lead_id": "lead-1",
        "canonical_key": "platform:lead-1",
        "fields": {"title": "Python integration", "budget": 1000, "category": "python"},
        "research_evidence": (
            evidence("e-title", "title", "Python integration"),
            evidence("e-budget", "budget", 1000),
            evidence("e-category", "category", "python"),
        ),
    }
    values.update(changes)
    return LeadRecord(**values)  # type: ignore[arg-type]


def validation_policy(**changes: object) -> ValidationPolicy:
    values: dict[str, object] = {
        "version": "validation-v1",
        "required_fields": ("title", "budget"),
        "allowed_source_types": frozenset({"fixture"}),
    }
    values.update(changes)
    return ValidationPolicy(**values)  # type: ignore[arg-type]


def qualification_policy(
    *criteria: Predicate, version: str = "qualification-v1"
) -> QualificationPolicy:
    selected = criteria or (
        Predicate(
            "budget-minimum",
            "budget",
            CriterionOperator.MINIMUM,
            500,
            evidence_required=True,
            allowed_source_types=frozenset({"fixture"}),
        ),
        Predicate(
            "category-allowed",
            "category",
            CriterionOperator.ALLOWED,
            ("python", "automation"),
        ),
    )
    return QualificationPolicy(version, tuple(selected))


def scoring_policy(*factors: ScoringFactor, version: str = "scoring-v1") -> ScoringPolicy:
    selected = factors or (
        ScoringFactor(
            "budget-fit",
            Predicate("budget-score", "budget", CriterionOperator.MINIMUM, 800),
            60,
        ),
        ScoringFactor(
            "category-fit",
            Predicate("category-score", "category", CriterionOperator.EQUALS, "python"),
            40,
        ),
    )
    return ScoringPolicy(version, tuple(selected))


def validated_lead(lead: LeadRecord | None = None) -> LeadRecord:
    selected = lead or found_lead()
    result = LeadValidator().evaluate(selected, validation_policy())
    assert result.outcome is ValidationOutcome.VALID
    return replace(selected, state=LeadState.VALIDATED, revision=1, validation=result)


def qualified_lead(lead: LeadRecord | None = None) -> LeadRecord:
    selected = validated_lead(lead)
    result = QualificationEvaluator().evaluate(selected, qualification_policy())
    assert result.outcome is QualificationOutcome.QUALIFIED
    return replace(selected, revision=2, qualification=result)


def test_valid_lead_validation_captures_policy_and_evidence() -> None:
    result = LeadValidator().evaluate(found_lead(), validation_policy())

    assert result.outcome is ValidationOutcome.VALID
    assert result.policy_version == "validation-v1"
    assert {item.check_id for item in result.evidence} == {
        "lead_state",
        "required_fields",
        "research_evidence",
        "source_references",
        "evidence_consistency",
    }


def test_missing_required_field_is_insufficient_not_invalid() -> None:
    lead = found_lead(fields={"title": "Python integration"}, research_evidence=())

    result = LeadValidator().evaluate(
        lead,
        validation_policy(require_research_evidence=False),
    )

    assert result.outcome is ValidationOutcome.INSUFFICIENT_INFORMATION
    assert result.missing_fields == ("budget",)


def test_invalid_state_is_invalid() -> None:
    lead = replace(found_lead(), state=LeadState.VALIDATED)

    result = LeadValidator().evaluate(lead, validation_policy())

    assert result.outcome is ValidationOutcome.INVALID
    assert result.evidence[0].result is EvidenceResult.INVALID


def test_missing_research_evidence_is_insufficient() -> None:
    result = LeadValidator().evaluate(
        found_lead(research_evidence=()),
        validation_policy(),
    )

    assert result.outcome is ValidationOutcome.INSUFFICIENT_INFORMATION


def test_invalid_source_reference_is_invalid() -> None:
    bad = evidence("bad", "budget", 1000, source_reference=source("bad", valid=False))
    lead = found_lead(research_evidence=(bad,))

    result = LeadValidator().evaluate(lead, validation_policy())

    assert result.outcome is ValidationOutcome.INVALID
    assert any(item.check_id == "source_references" for item in result.evidence)


def test_conflicting_evidence_is_invalid_and_preserved() -> None:
    lead = found_lead(
        research_evidence=(
            evidence("budget-a", "budget", 1000),
            evidence("budget-b", "budget", 900),
        )
    )

    result = LeadValidator().evaluate(lead, validation_policy())

    assert result.outcome is ValidationOutcome.INVALID
    assert result.conflicts == ("budget",)


def test_all_qualification_criteria_satisfied_with_structured_evidence() -> None:
    result = QualificationEvaluator().evaluate(validated_lead(), qualification_policy())

    assert result.outcome is QualificationOutcome.QUALIFIED
    assert result.policy_version == "qualification-v1"
    assert all(item.result is EvidenceResult.SATISFIED for item in result.criteria)
    budget = result.criteria[0]
    assert budget.observed_value == 1000
    assert budget.expected_condition == "MINIMUM:500"
    assert budget.source_references == ("e-budget",)


def test_criterion_failure_is_not_qualified_not_invalid() -> None:
    lead = found_lead(
        fields={"title": "Small task", "budget": 100, "category": "python"},
        research_evidence=(evidence("budget", "budget", 100),),
    )

    result = QualificationEvaluator().evaluate(validated_lead(lead), qualification_policy())

    assert result.outcome is QualificationOutcome.NOT_QUALIFIED


def test_missing_qualification_information_is_distinct() -> None:
    policy = qualification_policy(
        Predicate("timeline", "timeline_days", CriterionOperator.MAXIMUM, 30)
    )

    result = QualificationEvaluator().evaluate(validated_lead(), policy)

    assert result.outcome is QualificationOutcome.INSUFFICIENT_INFORMATION
    assert result.missing_information == ("timeline",)


def test_multiple_criterion_failures_are_all_recorded() -> None:
    lead = found_lead(
        fields={"title": "Other", "budget": 100, "category": "design"},
        research_evidence=(evidence("budget", "budget", 100),),
    )

    result = QualificationEvaluator().evaluate(validated_lead(lead), qualification_policy())

    assert result.outcome is QualificationOutcome.NOT_QUALIFIED
    assert [item.result for item in result.criteria] == [
        EvidenceResult.NOT_SATISFIED,
        EvidenceResult.NOT_SATISFIED,
    ]


def test_qualification_is_deterministic_and_policy_changes_result() -> None:
    lead = validated_lead()
    evaluator = QualificationEvaluator()
    permissive = qualification_policy(version="q-permissive")
    strict = qualification_policy(
        Predicate("budget-high", "budget", CriterionOperator.MINIMUM, 2000),
        version="q-strict",
    )

    first = evaluator.evaluate(lead, permissive)
    second = evaluator.evaluate(lead, permissive)
    changed = evaluator.evaluate(lead, strict)

    assert first == second
    assert first.outcome is QualificationOutcome.QUALIFIED
    assert changed.outcome is QualificationOutcome.NOT_QUALIFIED
    assert changed.policy_version == "q-strict"


@pytest.mark.parametrize(
    ("lead", "expected_score", "expected_contributions"),
    [
        (qualified_lead(), 100, (60, 40)),
        (
            qualified_lead(
                found_lead(
                    fields={
                        "title": "Python integration",
                        "budget": 600,
                        "category": "python",
                    },
                    research_evidence=(
                        evidence("budget", "budget", 600),
                        evidence("category", "category", "python"),
                    ),
                )
            ),
            40,
            (0, 40),
        ),
        (
            qualified_lead(
                found_lead(
                    fields={
                        "title": "Automation",
                        "budget": 600,
                        "category": "automation",
                    },
                    research_evidence=(
                        evidence("budget", "budget", 600),
                        evidence("category", "category", "automation"),
                    ),
                )
            ),
            0,
            (0, 0),
        ),
    ],
)
def test_scoring_records_all_partial_or_zero_contributions(
    lead: LeadRecord,
    expected_score: int,
    expected_contributions: tuple[int, ...],
) -> None:
    result = ScoringEvaluator().evaluate(lead, scoring_policy())

    assert result.outcome is ScoringOutcome.SCORED
    assert result.total_score == expected_score
    assert tuple(item.contribution for item in result.factors) == expected_contributions
    assert result.policy_version == "scoring-v1"


def test_scoring_is_deterministic_and_is_only_an_output() -> None:
    lead = qualified_lead()
    policy = scoring_policy()

    first = ScoringEvaluator().evaluate(lead, policy)
    second = ScoringEvaluator().evaluate(lead, policy)

    assert first == second
    assert not hasattr(first, "permission_id")
    assert not hasattr(first, "approval_id")
    assert not hasattr(first, "authorized")


def test_unvalidated_or_unqualified_lead_cannot_be_scored() -> None:
    result = ScoringEvaluator().evaluate(found_lead(), scoring_policy())

    assert result.outcome is ScoringOutcome.NOT_ELIGIBLE
    assert result.total_score == 0


def test_lead_records_reject_nested_secret_fields() -> None:
    with pytest.raises(FreelancingContractError):
        found_lead(fields={"nested": [{"access_token": "not-stored"}]})
