"""Deterministic failure classification and side-effect-aware recovery policy."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from zyro.core.errors import ErrorInfo
from zyro.recovery.contracts import (
    FailureClass,
    FailureIdentity,
    FailureRecord,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
    SideEffectState,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FailureClassifier:
    """Map existing structured errors into one canonical Phase 8 taxonomy."""

    @staticmethod
    def classify(
        failure_id: str,
        identity: FailureIdentity,
        error: ErrorInfo,
        occurred_at: datetime,
        *,
        component: str,
        outcome_unknown: bool = False,
        side_effect: SideEffectState = SideEffectState.NONE,
    ) -> FailureRecord:
        code = f"{error.code} {error.error_type}".lower()
        component_name = component.lower()
        if outcome_unknown or side_effect is SideEffectState.UNCERTAIN:
            classification = FailureClass.EXTERNAL_SIDE_EFFECT_UNCERTAIN
        elif "validation" in code or "invalid" in code:
            classification = FailureClass.VALIDATION_FAILURE
        elif "authoriz" in code or "permission" in code:
            classification = FailureClass.AUTHORIZATION_FAILURE
        elif "approval" in code:
            classification = FailureClass.APPROVAL_FAILURE
        elif "timeout" in code:
            classification = FailureClass.TIMEOUT
        elif "persist" in code or "sqlite" in code:
            classification = FailureClass.PERSISTENCE_FAILURE
        elif "resource" in code or "limit" in code or "budget" in code:
            classification = FailureClass.RESOURCE_EXHAUSTION
        elif "crash" in code or "interrupt" in code:
            classification = FailureClass.PROCESS_CRASH
        elif "verif" in code or component_name == "verification":
            classification = FailureClass.VERIFICATION_FAILURE
        elif "depend" in code:
            classification = FailureClass.DEPENDENCY_FAILURE
        elif "network" in code or "connection" in code:
            classification = FailureClass.NETWORK_FAILURE
        elif component_name == "model" or "model" in code or "provider" in code:
            classification = FailureClass.MODEL_FAILURE
        elif component_name == "tool" or "tool" in code:
            classification = FailureClass.TOOL_FAILURE
        else:
            classification = FailureClass.UNKNOWN_FAILURE
        return FailureRecord(
            failure_id,
            classification,
            identity,
            error,
            occurred_at,
            error.retryable,
            side_effect,
        )


class RecoveryPolicy:
    def __init__(
        self,
        *,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if base_backoff_seconds < 0 or max_backoff_seconds < base_backoff_seconds:
            raise ValueError("recovery backoff policy is invalid")
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._clock = clock

    def decide(self, request: RecoveryRequest) -> RecoveryDecision:
        failure = request.failure
        attempts_exhausted = request.attempt_count >= request.max_attempts
        next_attempt = request.attempt_count + 1

        if (
            failure.side_effect is SideEffectState.UNCERTAIN
            or failure.classification is FailureClass.EXTERNAL_SIDE_EFFECT_UNCERTAIN
        ):
            if request.reconciliation_available:
                return self._decision(
                    request,
                    RecoveryAction.RESUME,
                    "reconciliation_required",
                    "Resume only a safe reconciliation check; do not repeat the side effect.",
                    request.attempt_count,
                    reconciliation=True,
                )
            return self._decision(
                request,
                RecoveryAction.MARK_UNCERTAIN,
                "external_side_effect_uncertain",
                "External outcome is uncertain and cannot be retried safely.",
                request.attempt_count,
                reconciliation=True,
            )

        if request.hard_resource_limit:
            return self._decision(
                request,
                RecoveryAction.STOP,
                "hard_resource_limit_reached",
                "A hard resource limit requires work to stop without budget reset.",
                request.attempt_count,
            )
        if not request.resource_available:
            return self._decision(
                request,
                RecoveryAction.WAIT,
                "resource_capacity_unavailable",
                "Capacity is temporarily unavailable; work remains waiting.",
                request.attempt_count,
                backoff=self._backoff(request.attempt_count),
            )

        if (
            failure.classification is FailureClass.VERIFICATION_FAILURE
            and failure.side_effect is SideEffectState.CONFIRMED
        ):
            if request.reconciliation_available:
                return self._decision(
                    request,
                    RecoveryAction.RESUME,
                    "resume_verification",
                    "Resume verification without re-executing the confirmed side effect.",
                    request.attempt_count,
                    reconciliation=True,
                )
            return self._decision(
                request,
                RecoveryAction.ESCALATE,
                "verification_requires_operator",
                "Execution may already have occurred and cannot be verified automatically.",
                request.attempt_count,
            )

        if failure.classification in {
            FailureClass.AUTHORIZATION_FAILURE,
            FailureClass.VALIDATION_FAILURE,
        }:
            return self._decision(
                request,
                RecoveryAction.STOP,
                "non_retryable_policy_failure",
                "Validation or authorization failure cannot be recovered by retry.",
                request.attempt_count,
            )
        if failure.classification is FailureClass.APPROVAL_FAILURE:
            return self._decision(
                request,
                RecoveryAction.ESCALATE,
                "approval_attention_required",
                "Approval failure requires explicit attention and grants no authority.",
                request.attempt_count,
            )

        safe_to_repeat = (
            failure.side_effect
            in {
                SideEffectState.NONE,
                SideEffectState.NOT_STARTED,
            }
            or request.idempotent
        )
        if not safe_to_repeat:
            return self._decision(
                request,
                RecoveryAction.ESCALATE,
                "repeat_not_safe",
                "The operation may have a non-idempotent side effect.",
                request.attempt_count,
            )
        if attempts_exhausted:
            if request.fallback_available and failure.classification in {
                FailureClass.MODEL_FAILURE,
                FailureClass.TOOL_FAILURE,
                FailureClass.DEPENDENCY_FAILURE,
            }:
                return self._decision(
                    request,
                    RecoveryAction.FALLBACK,
                    "fallback_after_exhaustion",
                    "Configured compatible alternatives may be selected by their owner.",
                    request.attempt_count,
                )
            return self._decision(
                request,
                RecoveryAction.STOP,
                "recovery_attempts_exhausted",
                "Finite recovery attempts are exhausted.",
                request.attempt_count,
            )
        if failure.retryable:
            return self._decision(
                request,
                RecoveryAction.RETRY,
                "bounded_retry",
                "Failure is retryable, repeat-safe, within attempts, and has resources.",
                next_attempt,
                backoff=self._backoff(request.attempt_count),
            )
        if request.fallback_available and failure.classification in {
            FailureClass.MODEL_FAILURE,
            FailureClass.TOOL_FAILURE,
            FailureClass.DEPENDENCY_FAILURE,
        }:
            return self._decision(
                request,
                RecoveryAction.FALLBACK,
                "configured_fallback",
                "A registered compatible fallback may be selected by its existing router.",
                request.attempt_count,
            )
        return self._decision(
            request,
            RecoveryAction.STOP,
            "failure_not_retryable",
            "Failure is not retryable under current policy.",
            request.attempt_count,
        )

    def _backoff(self, attempt: int) -> float:
        return float(min(self._base_backoff * (2 ** max(attempt, 0)), self._max_backoff))

    def _decision(
        self,
        request: RecoveryRequest,
        action: RecoveryAction,
        code: str,
        reason: str,
        next_attempt: int,
        *,
        backoff: float = 0.0,
        reconciliation: bool = False,
    ) -> RecoveryDecision:
        return RecoveryDecision(
            request.recovery_id,
            request.operation_id,
            request.operation_revision,
            action,
            self._clock(),
            code,
            reason,
            next_attempt,
            backoff,
            reconciliation,
        )


__all__ = ["FailureClassifier", "RecoveryPolicy"]
