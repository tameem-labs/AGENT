"""Bounded deterministic Freelancing agents registered through the Core Agent Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.risk import RiskClass
from zyro.domains.freelancing.contracts import LeadRecord
from zyro.domains.freelancing.evaluation import (
    LeadValidator,
    QualificationEvaluator,
    ScoringEvaluator,
)
from zyro.domains.freelancing.policies import (
    QualificationPolicy,
    ScoringPolicy,
    ValidationPolicy,
)

VALIDATION_AGENT_ID = "freelancing.lead-validation"
QUALIFICATION_AGENT_ID = "freelancing.lead-qualification"
SCORING_AGENT_ID = "freelancing.lead-scoring"


class StageKind(StrEnum):
    VALIDATION = "VALIDATION"
    QUALIFICATION = "QUALIFICATION"
    SCORING = "SCORING"


StagePolicy: TypeAlias = ValidationPolicy | QualificationPolicy | ScoringPolicy


@dataclass(frozen=True, slots=True)
class StageInput:
    stage: StageKind
    lead: LeadRecord
    policy: StagePolicy


class StageInputRegistry:
    """Process-local request input seam; no generic Task state is duplicated here."""

    def __init__(self) -> None:
        self._inputs: dict[str, StageInput] = {}

    def bind(self, request_id: str, stage_input: StageInput) -> None:
        if request_id in self._inputs:
            raise ValueError(f"stage input is already bound: {request_id}")
        self._inputs[request_id] = stage_input

    def get(self, request_id: str) -> StageInput:
        try:
            return self._inputs[request_id]
        except KeyError as error:
            raise ValueError(f"stage input is not bound: {request_id}") from error

    def discard(self, request_id: str) -> None:
        self._inputs.pop(request_id, None)


class LeadValidationAgent:
    def __init__(self, inputs: StageInputRegistry, evaluator: LeadValidator) -> None:
        self._inputs = inputs
        self._evaluator = evaluator

    def execute(self, context: ExecutionContext) -> AgentExecution:
        stage_input = self._inputs.get(context.request_id)
        if stage_input.stage is not StageKind.VALIDATION or not isinstance(
            stage_input.policy, ValidationPolicy
        ):
            raise ValueError("validation agent received incompatible stage input")
        return AgentExecution.success(
            self._evaluator.evaluate(stage_input.lead, stage_input.policy)
        )


class LeadQualificationAgent:
    def __init__(self, inputs: StageInputRegistry, evaluator: QualificationEvaluator) -> None:
        self._inputs = inputs
        self._evaluator = evaluator

    def execute(self, context: ExecutionContext) -> AgentExecution:
        stage_input = self._inputs.get(context.request_id)
        if stage_input.stage is not StageKind.QUALIFICATION or not isinstance(
            stage_input.policy, QualificationPolicy
        ):
            raise ValueError("qualification agent received incompatible stage input")
        return AgentExecution.success(
            self._evaluator.evaluate(stage_input.lead, stage_input.policy)
        )


class LeadScoringAgent:
    def __init__(self, inputs: StageInputRegistry, evaluator: ScoringEvaluator) -> None:
        self._inputs = inputs
        self._evaluator = evaluator

    def execute(self, context: ExecutionContext) -> AgentExecution:
        stage_input = self._inputs.get(context.request_id)
        if stage_input.stage is not StageKind.SCORING or not isinstance(
            stage_input.policy, ScoringPolicy
        ):
            raise ValueError("scoring agent received incompatible stage input")
        return AgentExecution.success(
            self._evaluator.evaluate(stage_input.lead, stage_input.policy)
        )


def register_freelancing_agents(
    registry: AgentRegistry,
    inputs: StageInputRegistry,
    *,
    validator: LeadValidator | None = None,
    qualifier: QualificationEvaluator | None = None,
    scorer: ScoringEvaluator | None = None,
) -> None:
    registry.register(
        AgentDefinition(
            agent_id=VALIDATION_AGENT_ID,
            name="Lead Validation Agent",
            version="1.0.0",
            role="Validate lead input before qualification",
            domain="freelancing",
            responsibilities=("evaluate configured lead validation policy",),
            capabilities=("freelancing.lead.validate",),
            risk_class=RiskClass.AUTOMATIC,
            input_requirements=("lead revision", "validation policy"),
            output_contract={"type": "ValidationResult"},
            verification_requirements=("reproduce against authoritative revision",),
        ),
        LeadValidationAgent(inputs, validator or LeadValidator()),
    )
    registry.register(
        AgentDefinition(
            agent_id=QUALIFICATION_AGENT_ID,
            name="Lead Qualification Agent",
            version="1.0.0",
            role="Evaluate validated leads against configured criteria",
            domain="freelancing",
            responsibilities=("qualify leads without outreach or authorization",),
            capabilities=("freelancing.lead.qualify",),
            risk_class=RiskClass.AUTOMATIC,
            input_requirements=("validated lead revision", "qualification policy"),
            output_contract={"type": "QualificationResult"},
            verification_requirements=("reproduce against authoritative revision",),
        ),
        LeadQualificationAgent(inputs, qualifier or QualificationEvaluator()),
    )
    registry.register(
        AgentDefinition(
            agent_id=SCORING_AGENT_ID,
            name="Lead Scoring Agent",
            version="1.0.0",
            role="Score qualified leads with configured factors",
            domain="freelancing",
            responsibilities=("calculate deterministic factor contributions",),
            capabilities=("freelancing.lead.score",),
            risk_class=RiskClass.AUTOMATIC,
            input_requirements=("qualified lead revision", "scoring policy"),
            output_contract={"type": "ScoringResult"},
            verification_requirements=("reproduce against authoritative revision",),
        ),
        LeadScoringAgent(inputs, scorer or ScoringEvaluator()),
    )


__all__ = [
    "QUALIFICATION_AGENT_ID",
    "SCORING_AGENT_ID",
    "VALIDATION_AGENT_ID",
    "LeadQualificationAgent",
    "LeadScoringAgent",
    "StageInput",
    "StageInputRegistry",
    "StageKind",
    "register_freelancing_agents",
]
