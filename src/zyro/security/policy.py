"""Deterministic risk policy kept separate from permission and approval state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from zyro.core.risk import RiskClass
from zyro.security.permission import PermissionDecision


class ApprovalRequirementOutcome(StrEnum):
    NOT_PERMITTED = "NOT_PERMITTED"
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"


@dataclass(frozen=True, slots=True)
class ApprovalRequirementDecision:
    outcome: ApprovalRequirementOutcome
    risk_class: RiskClass
    policy_version: str
    reason: str
    permission_decision_id: str

    @property
    def approval_required(self) -> bool:
        return self.outcome is ApprovalRequirementOutcome.REQUIRED


class RiskPolicy:
    """Map risk to an approval path; risk never creates standing permission."""

    def __init__(
        self,
        *,
        policy_version: str,
        policy_controlled_without_approval: frozenset[str] = frozenset(),
    ) -> None:
        if not policy_version.strip():
            raise ValueError("policy_version must be a non-empty string")
        self.policy_version = policy_version.strip()
        self._policy_controlled_without_approval = frozenset(policy_controlled_without_approval)

    def evaluate(
        self,
        permission: PermissionDecision,
        risk_class: RiskClass,
    ) -> ApprovalRequirementDecision:
        if not permission.allowed:
            return ApprovalRequirementDecision(
                ApprovalRequirementOutcome.NOT_PERMITTED,
                risk_class,
                self.policy_version,
                "Risk classification cannot override a denied permission.",
                permission.decision_id,
            )
        if risk_class is RiskClass.AUTOMATIC:
            return ApprovalRequirementDecision(
                ApprovalRequirementOutcome.NOT_REQUIRED,
                risk_class,
                self.policy_version,
                "Automatic-risk action has standing permission and needs no human approval.",
                permission.decision_id,
            )
        if (
            risk_class is RiskClass.POLICY_CONTROLLED
            and permission.capability in self._policy_controlled_without_approval
        ):
            return ApprovalRequirementDecision(
                ApprovalRequirementOutcome.NOT_REQUIRED,
                risk_class,
                self.policy_version,
                "Policy explicitly permits this capability without dynamic approval.",
                permission.decision_id,
            )
        return ApprovalRequirementDecision(
            ApprovalRequirementOutcome.REQUIRED,
            risk_class,
            self.policy_version,
            "Risk policy requires action-specific human approval.",
            permission.decision_id,
        )
