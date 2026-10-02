from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from zyro.core.risk import RiskClass
from zyro.security.approval import (
    ApprovalAction,
    ApprovalError,
    ApprovalService,
    ApprovalState,
    ApprovalValidityOutcome,
    digest_arguments,
)
from zyro.security.permission import (
    InvalidPermissionError,
    Permission,
    PermissionEvaluationRequest,
    PermissionEvaluator,
    PermissionScope,
    PermissionStatus,
    PermissionStore,
    PrincipalDirectory,
)
from zyro.security.policy import ApprovalRequirementOutcome, RiskPolicy

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def permission_evaluator(
    permission: Permission | None = None,
    *,
    principals: tuple[str, ...] = ("agent-1",),
    capabilities: frozenset[str] = frozenset({"read", "write"}),
) -> PermissionEvaluator:
    store = PermissionStore()
    if permission is not None:
        store.add(permission)
    return PermissionEvaluator(
        store,
        PrincipalDirectory(principals),
        capabilities,
        policy_version="policy-v1",
        clock=lambda: NOW,
        id_factory=lambda: "decision-1",
    )


def evaluation(
    *,
    principal: str = "agent-1",
    capability: str = "read",
    scope: PermissionScope | None = None,
    context: dict[str, object] | None = None,
) -> PermissionEvaluationRequest:
    return PermissionEvaluationRequest(
        principal_id=principal,
        capability=capability,
        scope=scope or PermissionScope(tool_id="reader", action="execute"),
        context=context or {},
    )


def grant(**changes: object) -> Permission:
    values: dict[str, object] = {
        "permission_id": "permission-1",
        "principal_id": "agent-1",
        "capability": "read",
        "scope": PermissionScope(tool_id="reader", action="execute"),
        "policy_version": "policy-v1",
        "issued_at": NOW - timedelta(hours=1),
    }
    values.update(changes)
    return Permission(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("evaluation_request", "reason"),
    [
        (evaluation(principal="unknown"), "unknown_principal"),
        (evaluation(capability="unknown"), "unknown_capability"),
        (evaluation(), "permission_missing"),
    ],
)
def test_permission_defaults_to_deny(
    evaluation_request: PermissionEvaluationRequest,
    reason: str,
) -> None:
    decision = permission_evaluator().evaluate(evaluation_request)

    assert not decision.allowed
    assert decision.reason_code == reason
    assert decision.decision_id == "decision-1"


def test_permission_is_bound_to_principal_capability_tool_and_action() -> None:
    evaluator = permission_evaluator(grant())

    assert evaluator.evaluate(evaluation()).allowed
    wrong_tool = evaluator.evaluate(
        evaluation(scope=PermissionScope(tool_id="writer", action="execute"))
    )
    wrong_capability = evaluator.evaluate(evaluation(capability="write"))
    wrong_action = evaluator.evaluate(
        evaluation(scope=PermissionScope(tool_id="reader", action="delete"))
    )

    assert wrong_tool.reason_code == "permission_scope_mismatch"
    assert wrong_capability.reason_code == "permission_missing"
    assert wrong_action.reason_code == "permission_scope_mismatch"


@pytest.mark.parametrize(
    ("permission", "reason"),
    [
        (grant(status=PermissionStatus.DISABLED), "permission_disabled"),
        (grant(status=PermissionStatus.REVOKED), "permission_revoked"),
        (grant(expires_at=NOW), "permission_expired"),
        (grant(policy_version="old-policy"), "permission_policy_mismatch"),
    ],
)
def test_inactive_stale_or_expired_permissions_deny(
    permission: Permission,
    reason: str,
) -> None:
    decision = permission_evaluator(permission).evaluate(evaluation())

    assert not decision.allowed
    assert decision.reason_code == reason


def test_permission_conditions_are_exact_and_revocation_is_immediate() -> None:
    store = PermissionStore()
    store.add(grant(conditions={"environment": "test"}))
    evaluator = PermissionEvaluator(
        store,
        PrincipalDirectory(("agent-1",)),
        frozenset({"read"}),
        policy_version="policy-v1",
        clock=lambda: NOW,
        id_factory=lambda: "decision-1",
    )

    denied = evaluator.evaluate(evaluation(context={"environment": "production"}))
    allowed = evaluator.evaluate(evaluation(context={"environment": "test"}))
    store.set_status("permission-1", PermissionStatus.REVOKED)
    revoked = evaluator.evaluate(evaluation(context={"environment": "test"}))

    assert denied.reason_code == "permission_conditions_not_met"
    assert allowed.allowed
    assert revoked.reason_code == "permission_revoked"


@pytest.mark.parametrize(
    ("risk", "outcome"),
    [
        (RiskClass.AUTOMATIC, ApprovalRequirementOutcome.NOT_REQUIRED),
        (RiskClass.POLICY_CONTROLLED, ApprovalRequirementOutcome.REQUIRED),
        (RiskClass.STRICT_AUTHORIZATION, ApprovalRequirementOutcome.REQUIRED),
    ],
)
def test_risk_policy_selects_path_without_granting_authority(
    risk: RiskClass,
    outcome: ApprovalRequirementOutcome,
) -> None:
    permission = permission_evaluator(grant()).evaluate(evaluation())

    decision = RiskPolicy(policy_version="policy-v1").evaluate(permission, risk)

    assert decision.outcome is outcome
    assert decision.permission_decision_id == permission.decision_id


def test_risk_policy_cannot_override_denied_permission() -> None:
    denied = permission_evaluator().evaluate(evaluation())

    decision = RiskPolicy(policy_version="policy-v1").evaluate(denied, RiskClass.AUTOMATIC)

    assert decision.outcome is ApprovalRequirementOutcome.NOT_PERMITTED


def action(
    *, target: str = "record-1", arguments: dict[str, object] | None = None
) -> ApprovalAction:
    return ApprovalAction(
        executor_id="agent-1",
        action="tool.execute:writer",
        capability="write",
        target=target,
        scope=PermissionScope(tool_id="writer", target=target, action="execute"),
        risk_class=RiskClass.STRICT_AUTHORIZATION,
        reason="Update the requested record.",
        expected_effect="The record value changes.",
        arguments_digest=digest_arguments(arguments or {"value": 1}),
        conditions={"environment": "test"},
    )


def approval_service() -> ApprovalService:
    return ApprovalService(
        frozenset({"human-1"}),
        clock=lambda: NOW,
        id_factory=lambda: "approval-1",
    )


def pending(service: ApprovalService) -> str:
    request = service.create_request(
        request_id="request-1",
        task_id="task-1",
        requester_id="owner-1",
        action=action(),
        policy_version="policy-v1",
        expires_at=NOW + timedelta(minutes=5),
    )
    return request.approval_id


def test_approval_is_pending_until_explicit_human_decision() -> None:
    service = approval_service()
    approval_id = pending(service)

    before = service.check(approval_id, action())
    approved = service.approve(approval_id, "human-1", "Reviewed exact action.")
    after = service.check(approval_id, action())

    assert before.outcome is ApprovalValidityOutcome.PENDING
    assert approved.state is ApprovalState.APPROVED
    assert approved.decision_principal_id == "human-1"
    assert approved.decided_at == NOW
    assert after.valid
    assert service.history(approval_id)[0].principal_id == "human-1"


def test_requester_executor_and_unknown_principal_cannot_self_approve() -> None:
    service = approval_service()

    for principal in ("owner-1", "agent-1", "invented-manager"):
        approval_id = pending(service)
        with pytest.raises(ApprovalError):
            service.approve(approval_id, principal, "Self-issued.")
        # Use a fresh deterministic service because identity collisions are rejected.
        service = approval_service()


def test_material_action_change_invalidates_approval() -> None:
    service = approval_service()
    approval_id = pending(service)
    service.approve(approval_id, "human-1", "Reviewed exact action.")

    changed_target = service.check(approval_id, action(target="record-2"))
    changed_arguments = service.check(approval_id, action(arguments={"value": 2}))

    assert changed_target.outcome is ApprovalValidityOutcome.ACTION_MISMATCH
    assert changed_arguments.outcome is ApprovalValidityOutcome.ACTION_MISMATCH


@pytest.mark.parametrize(
    ("transition", "outcome"),
    [
        ("deny", ApprovalValidityOutcome.DENIED),
        ("cancel", ApprovalValidityOutcome.CANCELLED),
        ("escalate", ApprovalValidityOutcome.PENDING),
    ],
)
def test_non_approval_states_never_authorize(
    transition: str,
    outcome: ApprovalValidityOutcome,
) -> None:
    service = approval_service()
    approval_id = pending(service)

    if transition == "deny":
        service.deny(approval_id, "human-1", "Not allowed.")
    elif transition == "cancel":
        service.cancel(approval_id, "owner-1", "No longer needed.")
    else:
        service.escalate(approval_id, "owner-1", "Needs specialist review.")

    validity = service.check(approval_id, action())
    assert validity.outcome is outcome
    assert not validity.valid


def test_expired_approval_records_explicit_non_human_expiry_event() -> None:
    current = NOW
    service = ApprovalService(
        frozenset({"human-1"}),
        clock=lambda: current,
        id_factory=lambda: "approval-1",
    )
    approval_id = pending(service)
    current = NOW + timedelta(minutes=10)

    validity = service.check(approval_id, action())

    assert validity.outcome is ApprovalValidityOutcome.EXPIRED
    record = service.history(approval_id)[0]
    assert record.principal_id == "system:expiry"


def test_escalation_remains_non_authorizing_until_human_approval() -> None:
    service = approval_service()
    approval_id = pending(service)
    service.escalate(approval_id, "owner-1", "Needs specialist review.")

    assert not service.check(approval_id, action()).valid
    approved = service.approve(approval_id, "human-1", "Specialist review complete.")

    assert approved.state is ApprovalState.APPROVED
    assert approved.escalation_principal_id == "owner-1"
    assert service.check(approval_id, action()).valid


def test_security_records_reject_nested_secret_fields() -> None:
    with pytest.raises(InvalidPermissionError):
        grant(metadata={"nested": [{"api_key": "must-not-be-stored"}]})

    with pytest.raises(ApprovalError):
        ApprovalAction(
            executor_id="agent-1",
            action="tool.execute:writer",
            capability="write",
            target="record-1",
            scope=PermissionScope(tool_id="writer", target="record-1"),
            risk_class=RiskClass.STRICT_AUTHORIZATION,
            reason="Update record.",
            expected_effect="Record changes.",
            arguments_digest=digest_arguments({"value": 1}),
            conditions={"nested": [{"access_token": "must-not-be-stored"}]},
        )
