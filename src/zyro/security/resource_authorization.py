"""Phase 7 resource authorization through the canonical permission evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from zyro.core.errors import ErrorInfo
from zyro.security.permission import (
    PermissionEvaluationRequest,
    PermissionEvaluator,
    PermissionScope,
)


@dataclass(frozen=True, slots=True)
class ResourceAuthorizationDecision:
    allowed: bool
    decision_id: str
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if self.allowed == (self.error is not None):
            raise ValueError("authorization errors are required only for denied decisions")


class ResourceAuthorizer(Protocol):
    def authorize(
        self,
        requester_id: str,
        capability: str,
        scope_target: str,
        action: str,
    ) -> ResourceAuthorizationDecision: ...


class PermissionResourceAuthorizer:
    """Adapter only; memory, state, knowledge, and context create no authority."""

    def __init__(self, evaluator: PermissionEvaluator) -> None:
        self._evaluator = evaluator

    def authorize(
        self,
        requester_id: str,
        capability: str,
        scope_target: str,
        action: str,
    ) -> ResourceAuthorizationDecision:
        decision = self._evaluator.evaluate(
            PermissionEvaluationRequest(
                principal_id=requester_id,
                capability=capability,
                scope=PermissionScope(target=scope_target, action=action),
                context={},
            )
        )
        if decision.allowed:
            return ResourceAuthorizationDecision(True, decision.decision_id)
        return ResourceAuthorizationDecision(False, decision.decision_id, decision.as_error())


__all__ = [
    "PermissionResourceAuthorizer",
    "ResourceAuthorizationDecision",
    "ResourceAuthorizer",
]
