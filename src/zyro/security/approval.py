"""Action-specific, expiring, explicitly decided approval contracts and service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from zyro.core.data import validate_record
from zyro.core.risk import RiskClass
from zyro.security.permission import PermissionScope


class ApprovalError(ValueError):
    """Raised for invalid approval contracts or state transitions."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clean(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApprovalError(f"{field_name} must be a non-empty string")
    return value.strip()


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _reject_sensitive_value(value: Any, field_name: str) -> None:
    if isinstance(value, Mapping):
        _reject_sensitive_fields(value, field_name)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive_value(item, field_name)


def _reject_sensitive_fields(value: Mapping[str, Any], field_name: str) -> None:
    forbidden = {"secret", "password", "credential", "api_key", "access_token"}
    for key, item in value.items():
        if key.lower().replace("-", "_") in forbidden:
            raise ApprovalError(f"{field_name} cannot contain secret fields")
        _reject_sensitive_value(item, field_name)


def digest_arguments(arguments: Mapping[str, Any]) -> str:
    """Bind approval without persisting potentially sensitive argument values."""
    try:
        encoded = json.dumps(_canonical_value(arguments), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ApprovalError("action arguments cannot be deterministically encoded") from error
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalAction:
    executor_id: str
    action: str
    capability: str
    target: str
    scope: PermissionScope
    risk_class: RiskClass
    reason: str
    expected_effect: str
    arguments_digest: str
    conditions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "executor_id",
            "action",
            "capability",
            "target",
            "reason",
            "expected_effect",
            "arguments_digest",
        ):
            object.__setattr__(self, field_name, _clean(getattr(self, field_name), field_name))
        if not isinstance(self.scope, PermissionScope):
            raise ApprovalError("scope must be a PermissionScope")
        if not isinstance(self.risk_class, RiskClass):
            raise ApprovalError("risk_class must be a RiskClass")
        if self.scope.target is not None and self.scope.target != self.target:
            raise ApprovalError("action target contradicts its bounded scope")
        _reject_sensitive_fields(self.conditions, "conditions")
        object.__setattr__(self, "conditions", _freeze_mapping(self.conditions))

    @property
    def fingerprint(self) -> str:
        payload = {
            "executor_id": self.executor_id,
            "action": self.action,
            "capability": self.capability,
            "target": self.target,
            "scope": {
                "tool_id": self.scope.tool_id,
                "target": self.scope.target,
                "resource_id": self.scope.resource_id,
                "domain": self.scope.domain,
                "action": self.scope.action,
            },
            "risk_class": self.risk_class.value,
            "reason": self.reason,
            "expected_effect": self.expected_effect,
            "arguments_digest": self.arguments_digest,
            "conditions": _canonical_value(self.conditions),
        }
        try:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ApprovalError("approval action cannot be deterministically encoded") from error
        return hashlib.sha256(encoded.encode()).hexdigest()


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    ESCALATED = "ESCALATED"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class ApprovalDecisionType(StrEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    ESCALATE = "ESCALATE"
    EXPIRE = "EXPIRE"
    SUPERSEDE = "SUPERSEDE"


@dataclass(frozen=True, slots=True)
class ApprovalDecisionRecord:
    approval_id: str
    decision: ApprovalDecisionType
    principal_id: str
    decided_at: datetime
    reason: str
    request_id: str
    task_id: str


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_id: str
    request_id: str
    task_id: str
    requester_id: str
    action: ApprovalAction
    policy_version: str
    requested_at: datetime
    expires_at: datetime
    workflow_id: str | None = None
    evidence: tuple[str, ...] = ()
    state: ApprovalState = ApprovalState.PENDING
    decision_principal_id: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    escalation_principal_id: str | None = None
    escalation_reason: str | None = None
    display: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "approval_id",
            "request_id",
            "task_id",
            "requester_id",
            "policy_version",
        ):
            object.__setattr__(self, field_name, _clean(getattr(self, field_name), field_name))
        if self.workflow_id is not None:
            object.__setattr__(self, "workflow_id", _clean(self.workflow_id, "workflow_id"))
        if self.requested_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ApprovalError("approval timestamps must be timezone-aware")
        if self.expires_at <= self.requested_at:
            raise ApprovalError("approval expiry must be after request time")
        if any(not item.strip() for item in self.evidence):
            raise ApprovalError("approval evidence entries must not be empty")
        try:
            object.__setattr__(
                self,
                "display",
                validate_record(self.display, "approval display", max_bytes=16_384),
            )
        except ValueError as error:
            raise ApprovalError(str(error)) from error
        decision_fields = (
            self.decision_principal_id,
            self.decided_at,
            self.decision_reason,
        )
        if self.state in {ApprovalState.PENDING, ApprovalState.ESCALATED}:
            if any(item is not None for item in decision_fields):
                raise ApprovalError("open approval cannot contain a final decision")
        elif any(item is None for item in decision_fields):
            raise ApprovalError("closed approval requires principal, time, and reason")
        has_escalation_principal = self.escalation_principal_id is not None
        has_escalation_reason = self.escalation_reason is not None
        if has_escalation_principal != has_escalation_reason:
            raise ApprovalError("escalation principal and reason must be recorded together")
        if self.state is ApprovalState.ESCALATED and not has_escalation_principal:
            raise ApprovalError("escalated approval requires principal and reason")
        if self.state is ApprovalState.PENDING and has_escalation_principal:
            raise ApprovalError("pending approval cannot contain escalation fields")

    @property
    def action_fingerprint(self) -> str:
        return self.action.fingerprint


class ApprovalValidityOutcome(StrEnum):
    VALID = "VALID"
    PENDING = "PENDING"
    DENIED = "DENIED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    ACTION_MISMATCH = "ACTION_MISMATCH"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class ApprovalValidity:
    outcome: ApprovalValidityOutcome
    approval: ApprovalRequest | None
    reason: str

    @property
    def valid(self) -> bool:
        return self.outcome is ApprovalValidityOutcome.VALID


class ApprovalService:
    """Deterministic in-process approval service; creation never means approval."""

    def __init__(
        self,
        decision_principals: frozenset[str],
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._decision_principals = frozenset(
            _clean(item, "decision_principal") for item in decision_principals
        )
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._requests: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalDecisionRecord] = []

    def create_request(
        self,
        *,
        request_id: str,
        task_id: str,
        requester_id: str,
        action: ApprovalAction,
        policy_version: str,
        expires_at: datetime,
        workflow_id: str | None = None,
        evidence: tuple[str, ...] = (),
        display: Mapping[str, Any] | None = None,
    ) -> ApprovalRequest:
        requested_at = self._clock()
        approval_id = self._id_factory()
        if approval_id in self._requests:
            raise ApprovalError(f"approval identity already exists: {approval_id}")
        request = ApprovalRequest(
            approval_id=approval_id,
            request_id=request_id,
            task_id=task_id,
            workflow_id=workflow_id,
            requester_id=requester_id,
            action=action,
            policy_version=policy_version,
            evidence=evidence,
            display={} if display is None else display,
            requested_at=requested_at,
            expires_at=expires_at,
        )
        self._requests[request.approval_id] = request
        return request

    def get(self, approval_id: str) -> ApprovalRequest:
        try:
            return self._requests[approval_id]
        except KeyError as error:
            raise ApprovalError(f"approval is not registered: {approval_id}") from error

    def history(self, approval_id: str | None = None) -> tuple[ApprovalDecisionRecord, ...]:
        return tuple(
            item for item in self._history if approval_id is None or item.approval_id == approval_id
        )

    def approve(
        self,
        approval_id: str,
        decision_principal_id: str,
        reason: str,
    ) -> ApprovalRequest:
        return self._decide(
            approval_id,
            decision_principal_id,
            reason,
            ApprovalDecisionType.APPROVE,
            ApprovalState.APPROVED,
        )

    def deny(
        self,
        approval_id: str,
        decision_principal_id: str,
        reason: str,
    ) -> ApprovalRequest:
        return self._decide(
            approval_id,
            decision_principal_id,
            reason,
            ApprovalDecisionType.DENY,
            ApprovalState.DENIED,
        )

    def reject(
        self,
        approval_id: str,
        decision_principal_id: str,
        reason: str,
    ) -> ApprovalRequest:
        return self._decide(
            approval_id,
            decision_principal_id,
            reason,
            ApprovalDecisionType.REJECT,
            ApprovalState.REJECTED,
        )

    def escalate(
        self,
        approval_id: str,
        escalation_principal_id: str,
        reason: str,
    ) -> ApprovalRequest:
        request = self._current_open(approval_id)
        now = self._clock()
        updated = replace(
            request,
            state=ApprovalState.ESCALATED,
            escalation_principal_id=_clean(escalation_principal_id, "escalation_principal_id"),
            escalation_reason=_clean(reason, "reason"),
        )
        self._requests[approval_id] = updated
        self._record(updated, ApprovalDecisionType.ESCALATE, escalation_principal_id, now, reason)
        return updated

    def cancel(self, approval_id: str, principal_id: str, reason: str) -> ApprovalRequest:
        request = self._current_open(approval_id)
        principal_id = _clean(principal_id, "principal_id")
        if principal_id not in {request.requester_id, request.action.executor_id} and (
            principal_id not in self._decision_principals
        ):
            raise ApprovalError("principal cannot cancel this approval")
        return self._transition(
            request,
            ApprovalState.CANCELLED,
            ApprovalDecisionType.CANCEL,
            principal_id,
            reason,
        )

    def supersede(self, approval_id: str, principal_id: str, reason: str) -> ApprovalRequest:
        request = self._current_open(approval_id)
        if principal_id not in self._decision_principals:
            raise ApprovalError("only an approval decision principal can supersede")
        return self._transition(
            request,
            ApprovalState.SUPERSEDED,
            ApprovalDecisionType.SUPERSEDE,
            principal_id,
            reason,
        )

    def check(
        self,
        approval_id: str,
        action: ApprovalAction,
    ) -> ApprovalValidity:
        request = self._requests.get(approval_id)
        if request is None:
            return ApprovalValidity(
                ApprovalValidityOutcome.NOT_FOUND,
                None,
                "Approval does not exist.",
            )
        request = self._expire_if_needed(request)
        if request.action_fingerprint != action.fingerprint:
            return ApprovalValidity(
                ApprovalValidityOutcome.ACTION_MISMATCH,
                request,
                "Approval is bound to a materially different action.",
            )
        outcomes = {
            ApprovalState.PENDING: ApprovalValidityOutcome.PENDING,
            ApprovalState.ESCALATED: ApprovalValidityOutcome.PENDING,
            ApprovalState.APPROVED: ApprovalValidityOutcome.VALID,
            ApprovalState.DENIED: ApprovalValidityOutcome.DENIED,
            ApprovalState.REJECTED: ApprovalValidityOutcome.REJECTED,
            ApprovalState.CANCELLED: ApprovalValidityOutcome.CANCELLED,
            ApprovalState.EXPIRED: ApprovalValidityOutcome.EXPIRED,
            ApprovalState.SUPERSEDED: ApprovalValidityOutcome.SUPERSEDED,
        }
        outcome = outcomes[request.state]
        return ApprovalValidity(outcome, request, f"Approval state is {request.state}.")

    def _decide(
        self,
        approval_id: str,
        principal_id: str,
        reason: str,
        decision: ApprovalDecisionType,
        state: ApprovalState,
    ) -> ApprovalRequest:
        principal_id = _clean(principal_id, "decision_principal_id")
        reason = _clean(reason, "reason")
        request = self._expire_if_needed(self.get(approval_id))
        if principal_id not in self._decision_principals:
            raise ApprovalError("decision principal is not authorized to decide approvals")
        if principal_id in {request.requester_id, request.action.executor_id}:
            raise ApprovalError("requester or executor cannot approve its own action")
        if request.state is state:
            if request.decision_principal_id == principal_id and request.decision_reason == reason:
                return request
            raise ApprovalError("approval already has a conflicting final decision")
        if request.state not in {ApprovalState.PENDING, ApprovalState.ESCALATED}:
            raise ApprovalError(f"approval cannot transition from {request.state}")
        return self._transition(request, state, decision, principal_id, reason)

    def _current_open(self, approval_id: str) -> ApprovalRequest:
        request = self._expire_if_needed(self.get(approval_id))
        if request.state not in {ApprovalState.PENDING, ApprovalState.ESCALATED}:
            raise ApprovalError(f"approval cannot transition from {request.state}")
        return request

    def _expire_if_needed(self, request: ApprovalRequest) -> ApprovalRequest:
        now = self._clock()
        if (
            request.state
            in {
                ApprovalState.PENDING,
                ApprovalState.ESCALATED,
                ApprovalState.APPROVED,
            }
            and now >= request.expires_at
        ):
            return self._transition(
                request,
                ApprovalState.EXPIRED,
                ApprovalDecisionType.EXPIRE,
                "system:expiry",
                "Approval validity period expired.",
                decided_at=now,
            )
        return request

    def _transition(
        self,
        request: ApprovalRequest,
        state: ApprovalState,
        decision: ApprovalDecisionType,
        principal_id: str,
        reason: str,
        *,
        decided_at: datetime | None = None,
    ) -> ApprovalRequest:
        now = self._clock() if decided_at is None else decided_at
        principal_id = _clean(principal_id, "principal_id")
        reason = _clean(reason, "reason")
        updated = replace(
            request,
            state=state,
            decision_principal_id=principal_id,
            decided_at=now,
            decision_reason=reason,
        )
        self._requests[request.approval_id] = updated
        self._record(updated, decision, principal_id, now, reason)
        return updated

    def _record(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecisionType,
        principal_id: str,
        decided_at: datetime,
        reason: str,
    ) -> None:
        self._history.append(
            ApprovalDecisionRecord(
                approval_id=request.approval_id,
                decision=decision,
                principal_id=principal_id,
                decided_at=decided_at,
                reason=reason,
                request_id=request.request_id,
                task_id=request.task_id,
            )
        )
