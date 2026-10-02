"""Permission + risk + approval orchestration for bounded tool actions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from zyro.core.errors import ErrorInfo
from zyro.security.approval import (
    ApprovalAction,
    ApprovalService,
    ApprovalValidityOutcome,
    digest_arguments,
)
from zyro.security.permission import (
    PermissionDecision,
    PermissionDecisionOutcome,
    PermissionEvaluationRequest,
    PermissionEvaluator,
    PermissionScope,
)
from zyro.security.policy import ApprovalRequirementOutcome, RiskPolicy
from zyro.tools.contracts import (
    ToolAuthorizationDecision,
    ToolAuthorizationStatus,
    ToolCall,
    ToolDefinition,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ToolAuthorizationService:
    """Authorize one concrete action before ToolExecutor may invoke its handler."""

    def __init__(
        self,
        permissions: PermissionEvaluator,
        risk_policy: RiskPolicy,
        approvals: ApprovalService,
        *,
        approval_ttl: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._permissions = permissions
        self._risk_policy = risk_policy
        self._approvals = approvals
        self._approval_ttl = approval_ttl
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def authorize(
        self,
        call: ToolCall,
        definition: ToolDefinition,
    ) -> ToolAuthorizationDecision:
        capability = self._resolve_capability(call, definition)
        scope = PermissionScope(
            tool_id=definition.tool_id,
            target=call.target,
            action="execute",
        )
        if capability is None:
            decision = self._local_deny(
                call,
                scope,
                "tool_capability_invalid",
                "Tool call does not identify a capability exposed by the tool.",
            )
            return self._permission_denied(decision)

        permission = self._permissions.evaluate(
            PermissionEvaluationRequest(
                principal_id=call.agent_id,
                capability=capability,
                scope=scope,
                context=call.conditions,
            )
        )
        if not permission.allowed:
            return self._permission_denied(permission)

        risk = self._risk_policy.evaluate(permission, definition.risk_class)
        if risk.outcome is ApprovalRequirementOutcome.NOT_PERMITTED:
            return self._permission_denied(permission)
        if risk.outcome is ApprovalRequirementOutcome.NOT_REQUIRED:
            return ToolAuthorizationDecision(
                status=ToolAuthorizationStatus.ALLOW,
                permission_decision_id=permission.decision_id,
                permission_id=permission.permission_id,
                policy_version=risk.policy_version,
            )

        action_error = self._approval_action_error(call)
        if action_error is not None:
            return ToolAuthorizationDecision(
                status=ToolAuthorizationStatus.APPROVAL_INVALID,
                permission_decision_id=permission.decision_id,
                permission_id=permission.permission_id,
                policy_version=risk.policy_version,
                error=ErrorInfo(
                    code="approval_action_incomplete",
                    message=action_error,
                    error_type="ApprovalInvalid",
                ),
            )
        assert call.requester_id is not None
        assert call.target is not None
        assert call.purpose is not None
        assert call.expected_effect is not None
        action = ApprovalAction(
            executor_id=call.agent_id,
            action=f"tool.execute:{definition.tool_id}",
            capability=capability,
            target=call.target,
            scope=scope,
            risk_class=definition.risk_class,
            reason=call.purpose,
            expected_effect=call.expected_effect,
            arguments_digest=digest_arguments(call.arguments),
            conditions=call.conditions,
        )

        if call.approval_id is None:
            approval = self._approvals.create_request(
                request_id=call.request_id,
                task_id=call.task_id,
                requester_id=call.requester_id,
                action=action,
                policy_version=risk.policy_version,
                expires_at=self._clock() + self._approval_ttl,
                workflow_id=call.workflow_id,
                evidence=(
                    f"permission_decision:{permission.decision_id}",
                    f"risk:{definition.risk_class.value}",
                ),
                display=call.approval_context,
            )
            return ToolAuthorizationDecision(
                status=ToolAuthorizationStatus.APPROVAL_PENDING,
                permission_decision_id=permission.decision_id,
                permission_id=permission.permission_id,
                approval_id=approval.approval_id,
                policy_version=risk.policy_version,
                error=ErrorInfo(
                    code="approval_pending",
                    message="Action-specific human approval is pending.",
                    error_type="ApprovalPending",
                ),
            )

        validity = self._approvals.check(call.approval_id, action)
        if validity.valid:
            return ToolAuthorizationDecision(
                status=ToolAuthorizationStatus.ALLOW,
                permission_decision_id=permission.decision_id,
                permission_id=permission.permission_id,
                approval_id=call.approval_id,
                policy_version=risk.policy_version,
            )

        status, code = {
            ApprovalValidityOutcome.PENDING: (
                ToolAuthorizationStatus.APPROVAL_PENDING,
                "approval_pending",
            ),
            ApprovalValidityOutcome.DENIED: (
                ToolAuthorizationStatus.APPROVAL_DENIED,
                "approval_denied",
            ),
            ApprovalValidityOutcome.REJECTED: (
                ToolAuthorizationStatus.APPROVAL_DENIED,
                "approval_rejected",
            ),
            ApprovalValidityOutcome.EXPIRED: (
                ToolAuthorizationStatus.APPROVAL_EXPIRED,
                "approval_expired",
            ),
            ApprovalValidityOutcome.CANCELLED: (
                ToolAuthorizationStatus.APPROVAL_DENIED,
                "approval_cancelled",
            ),
            ApprovalValidityOutcome.SUPERSEDED: (
                ToolAuthorizationStatus.APPROVAL_INVALID,
                "approval_superseded",
            ),
            ApprovalValidityOutcome.ACTION_MISMATCH: (
                ToolAuthorizationStatus.APPROVAL_INVALID,
                "approval_action_mismatch",
            ),
            ApprovalValidityOutcome.NOT_FOUND: (
                ToolAuthorizationStatus.APPROVAL_INVALID,
                "approval_not_found",
            ),
            ApprovalValidityOutcome.VALID: (
                ToolAuthorizationStatus.ALLOW,
                "approval_valid",
            ),
        }[validity.outcome]
        assert status is not ToolAuthorizationStatus.ALLOW
        return ToolAuthorizationDecision(
            status=status,
            permission_decision_id=permission.decision_id,
            permission_id=permission.permission_id,
            approval_id=call.approval_id,
            policy_version=risk.policy_version,
            error=ErrorInfo(
                code=code,
                message=validity.reason,
                error_type="ApprovalBlocked",
            ),
        )

    @staticmethod
    def _resolve_capability(call: ToolCall, definition: ToolDefinition) -> str | None:
        if call.capability is not None:
            return call.capability if call.capability in definition.capabilities else None
        if len(definition.capabilities) == 1:
            return next(iter(definition.capabilities))
        return None

    @staticmethod
    def _approval_action_error(call: ToolCall) -> str | None:
        required = {
            "requester_id": call.requester_id,
            "target": call.target,
            "purpose": call.purpose,
            "expected_effect": call.expected_effect,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            return f"Approval-required action is missing: {', '.join(missing)}."
        return None

    @staticmethod
    def _permission_denied(permission: PermissionDecision) -> ToolAuthorizationDecision:
        return ToolAuthorizationDecision(
            status=ToolAuthorizationStatus.PERMISSION_DENIED,
            permission_decision_id=permission.decision_id,
            permission_id=permission.permission_id,
            policy_version=permission.policy_version,
            error=permission.as_error(),
        )

    def _local_deny(
        self,
        call: ToolCall,
        scope: PermissionScope,
        code: str,
        reason: str,
    ) -> PermissionDecision:
        return PermissionDecision(
            decision_id=self._id_factory(),
            outcome=PermissionDecisionOutcome.DENY,
            principal_id=call.agent_id,
            capability=call.capability or "unknown",
            requested_scope=scope,
            reason_code=code,
            reason=reason,
            policy_version=self._risk_policy.policy_version,
            evaluated_at=self._clock(),
        )
