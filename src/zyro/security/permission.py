"""Explicit, scoped, default-deny permission contracts and evaluation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from zyro.core.errors import ErrorInfo, ZyroError


class InvalidPermissionError(ZyroError, ValueError):
    """Raised when a permission contract is invalid."""


class DuplicatePermissionError(ZyroError, ValueError):
    """Raised when a permission identity is already registered."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clean(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidPermissionError(f"{field_name} must be a non-empty string")
    return value.strip()


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


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
            raise InvalidPermissionError(f"{field_name} cannot contain secret fields")
        _reject_sensitive_value(item, field_name)


@dataclass(frozen=True, slots=True)
class PermissionScope:
    """Bounded authority dimensions; omitted dimensions are explicit wildcards."""

    tool_id: str | None = None
    target: str | None = None
    resource_id: str | None = None
    domain: str | None = None
    action: str | None = None

    def __post_init__(self) -> None:
        populated = False
        for field_name in ("tool_id", "target", "resource_id", "domain", "action"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _clean(value, field_name))
                populated = True
        if not populated:
            raise InvalidPermissionError("permission scope must constrain at least one dimension")

    def contains(self, requested: PermissionScope) -> bool:
        """Return whether this grant contains every explicitly bounded request dimension."""
        for field_name in ("tool_id", "target", "resource_id", "domain", "action"):
            granted_value = getattr(self, field_name)
            requested_value = getattr(requested, field_name)
            if granted_value is not None and granted_value != requested_value:
                return False
        return True


class PermissionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class Permission:
    permission_id: str
    principal_id: str
    capability: str
    scope: PermissionScope
    policy_version: str
    status: PermissionStatus = PermissionStatus.ACTIVE
    conditions: Mapping[str, Any] = field(default_factory=dict)
    issued_at: datetime = field(default_factory=_utc_now)
    expires_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("permission_id", "principal_id", "capability", "policy_version"):
            object.__setattr__(self, field_name, _clean(getattr(self, field_name), field_name))
        if not isinstance(self.status, PermissionStatus):
            raise InvalidPermissionError("status must be a PermissionStatus")
        if not isinstance(self.scope, PermissionScope):
            raise InvalidPermissionError("scope must be a PermissionScope")
        if self.issued_at.tzinfo is None:
            raise InvalidPermissionError("issued_at must be timezone-aware")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None:
                raise InvalidPermissionError("expires_at must be timezone-aware")
            if self.expires_at <= self.issued_at:
                raise InvalidPermissionError("expires_at must be after issued_at")
        _reject_sensitive_fields(self.conditions, "conditions")
        _reject_sensitive_fields(self.metadata, "metadata")
        object.__setattr__(self, "conditions", _freeze_mapping(self.conditions))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


class PermissionDecisionOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    decision_id: str
    outcome: PermissionDecisionOutcome
    principal_id: str
    capability: str
    requested_scope: PermissionScope
    reason_code: str
    reason: str
    policy_version: str
    evaluated_at: datetime
    permission_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome is PermissionDecisionOutcome.ALLOW

    def as_error(self) -> ErrorInfo:
        return ErrorInfo(
            code=self.reason_code,
            message=self.reason,
            error_type="PermissionDenied",
        )


@dataclass(frozen=True, slots=True)
class PermissionEvaluationRequest:
    principal_id: str
    capability: str
    scope: PermissionScope
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "principal_id", _clean(self.principal_id, "principal_id"))
        object.__setattr__(self, "capability", _clean(self.capability, "capability"))
        if not isinstance(self.scope, PermissionScope):
            raise InvalidPermissionError("scope must be a PermissionScope")
        _reject_sensitive_fields(self.context, "context")
        object.__setattr__(self, "context", _freeze_mapping(self.context))


class PrincipalDirectory:
    """Known principal identifiers only; this is not authentication or session proof."""

    def __init__(self, principal_ids: tuple[str, ...] = ()) -> None:
        self._principal_ids: set[str] = set()
        for principal_id in principal_ids:
            self.register(principal_id)

    def register(self, principal_id: str) -> None:
        self._principal_ids.add(_clean(principal_id, "principal_id"))

    def contains(self, principal_id: str) -> bool:
        return principal_id in self._principal_ids


class PermissionStore:
    """Revocable in-process permission records with stable identities."""

    def __init__(self) -> None:
        self._permissions: dict[str, Permission] = {}

    def add(self, permission: Permission) -> None:
        if permission.permission_id in self._permissions:
            raise DuplicatePermissionError(
                f"permission is already registered: {permission.permission_id}"
            )
        self._permissions[permission.permission_id] = permission

    def get(self, permission_id: str) -> Permission:
        try:
            return self._permissions[permission_id]
        except KeyError as error:
            raise InvalidPermissionError(
                f"permission is not registered: {permission_id}"
            ) from error

    def list_for(self, principal_id: str, capability: str) -> tuple[Permission, ...]:
        return tuple(
            permission
            for permission in sorted(
                self._permissions.values(), key=lambda item: item.permission_id
            )
            if permission.principal_id == principal_id and permission.capability == capability
        )

    def set_status(self, permission_id: str, status: PermissionStatus) -> Permission:
        updated = replace(self.get(permission_id), status=status)
        self._permissions[permission_id] = updated
        return updated


class PermissionEvaluator:
    """Evaluate standing authority with explicit default-deny behavior."""

    def __init__(
        self,
        store: PermissionStore,
        principals: PrincipalDirectory,
        known_capabilities: frozenset[str],
        *,
        policy_version: str,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._principals = principals
        self._known_capabilities = frozenset(known_capabilities)
        self._policy_version = _clean(policy_version, "policy_version")
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def evaluate(self, request: PermissionEvaluationRequest) -> PermissionDecision:
        now = self._clock()
        if not self._principals.contains(request.principal_id):
            return self._deny(request, "unknown_principal", "Principal is not known.", now)
        if request.capability not in self._known_capabilities:
            return self._deny(request, "unknown_capability", "Capability is not known.", now)

        candidates = self._store.list_for(request.principal_id, request.capability)
        if not candidates:
            return self._deny(
                request, "permission_missing", "No permission grants capability.", now
            )
        scoped = [
            permission for permission in candidates if permission.scope.contains(request.scope)
        ]
        if not scoped:
            return self._deny(
                request,
                "permission_scope_mismatch",
                "Permission does not contain the requested scope.",
                now,
            )

        denial: tuple[str, str, Permission] | None = None
        for permission in scoped:
            if permission.policy_version != self._policy_version:
                denial = (
                    "permission_policy_mismatch",
                    "Permission was issued under a different policy version.",
                    permission,
                )
                continue
            if permission.status is PermissionStatus.DISABLED:
                denial = ("permission_disabled", "Permission is disabled.", permission)
                continue
            if permission.status is PermissionStatus.REVOKED:
                denial = ("permission_revoked", "Permission is revoked.", permission)
                continue
            if permission.expires_at is not None and now >= permission.expires_at:
                denial = ("permission_expired", "Permission has expired.", permission)
                continue
            if any(
                request.context.get(key) != value for key, value in permission.conditions.items()
            ):
                denial = (
                    "permission_conditions_not_met",
                    "Permission conditions are not satisfied.",
                    permission,
                )
                continue
            return PermissionDecision(
                decision_id=self._id_factory(),
                outcome=PermissionDecisionOutcome.ALLOW,
                principal_id=request.principal_id,
                capability=request.capability,
                requested_scope=request.scope,
                reason_code="permission_granted",
                reason="An active scoped permission grants this capability.",
                policy_version=permission.policy_version,
                evaluated_at=now,
                permission_id=permission.permission_id,
            )

        assert denial is not None
        code, reason, permission = denial
        return self._deny(request, code, reason, now, permission)

    def _deny(
        self,
        request: PermissionEvaluationRequest,
        code: str,
        reason: str,
        now: datetime,
        permission: Permission | None = None,
    ) -> PermissionDecision:
        return PermissionDecision(
            decision_id=self._id_factory(),
            outcome=PermissionDecisionOutcome.DENY,
            principal_id=request.principal_id,
            capability=request.capability,
            requested_scope=request.scope,
            reason_code=code,
            reason=reason,
            policy_version=(
                self._policy_version if permission is None else permission.policy_version
            ),
            evaluated_at=now,
            permission_id=None if permission is None else permission.permission_id,
        )
