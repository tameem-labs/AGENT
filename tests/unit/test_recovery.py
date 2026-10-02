from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from zyro.core.errors import ErrorInfo
from zyro.core.events import InProcessEventPublisher
from zyro.core.task import Task, TaskStatus
from zyro.recovery import (
    FailureClass,
    FailureClassifier,
    FailureIdentity,
    FailureRecord,
    RecoverableOperation,
    RecoverableOperationStatus,
    RecoveryAction,
    RecoveryPolicy,
    RecoveryRequest,
    SideEffectState,
    SQLiteRecoveryStore,
    StartupReconciler,
    TaskRecoveryAdapter,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
IDENTITY = FailureIdentity(
    "request-1",
    "task-1",
    "correlation-1",
    "workflow-1",
    "agent-1",
    "instance-1",
    "tool-1",
    "model-1",
    approval_id="approval-1",
    verification_id="verification-1",
    message_id="message-1",
    event_id="event-1",
    component_id="component-1",
)


def failure(
    classification: FailureClass,
    *,
    retryable: bool = True,
    side_effect: SideEffectState = SideEffectState.NONE,
) -> FailureRecord:
    return FailureRecord(
        "failure-1",
        classification,
        IDENTITY,
        ErrorInfo("fixture_failure", "Bounded fixture failure.", "FixtureFailure", retryable),
        NOW,
        retryable,
        side_effect,
    )


def request(
    item: FailureRecord,
    **changes: object,
) -> RecoveryRequest:
    values: dict[str, object] = {
        "recovery_id": "recovery-1",
        "operation_id": "operation-1",
        "failure": item,
        "attempt_count": 1,
        "max_attempts": 3,
        "idempotent": True,
        "resource_available": True,
        "hard_resource_limit": False,
        "fallback_available": False,
        "reconciliation_available": False,
        "current_task_state": "FAILED",
    }
    values.update(changes)
    return RecoveryRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("component", "error", "expected"),
    [
        (
            "request",
            ErrorInfo("invalid_input", "Invalid.", "Validation"),
            FailureClass.VALIDATION_FAILURE,
        ),
        (
            "security",
            ErrorInfo("permission_denied", "Denied.", "PermissionDenied"),
            FailureClass.AUTHORIZATION_FAILURE,
        ),
        (
            "approval",
            ErrorInfo("approval_expired", "Expired.", "ApprovalExpired"),
            FailureClass.APPROVAL_FAILURE,
        ),
        (
            "model",
            ErrorInfo("provider_failure", "Failed.", "ProviderFailure"),
            FailureClass.MODEL_FAILURE,
        ),
        ("tool", ErrorInfo("tool_failure", "Failed.", "ToolFailure"), FailureClass.TOOL_FAILURE),
        (
            "network",
            ErrorInfo("connection_failure", "Failed.", "NetworkFailure"),
            FailureClass.NETWORK_FAILURE,
        ),
        (
            "runtime",
            ErrorInfo("operation_timeout", "Timed out.", "TimeoutError"),
            FailureClass.TIMEOUT,
        ),
        (
            "store",
            ErrorInfo("persistence_failure", "Failed.", "StoreFailure"),
            FailureClass.PERSISTENCE_FAILURE,
        ),
        (
            "resource",
            ErrorInfo("resource_limit", "Reached.", "ResourceExhaustion"),
            FailureClass.RESOURCE_EXHAUSTION,
        ),
        ("runtime", ErrorInfo("process_crash", "Crashed.", "Crash"), FailureClass.PROCESS_CRASH),
        (
            "verification",
            ErrorInfo("check_failed", "Failed.", "Check"),
            FailureClass.VERIFICATION_FAILURE,
        ),
        (
            "runtime",
            ErrorInfo("dependency_failed", "Failed.", "Dependency"),
            FailureClass.DEPENDENCY_FAILURE,
        ),
        ("runtime", ErrorInfo("other", "Failed.", "Other"), FailureClass.UNKNOWN_FAILURE),
    ],
)
def test_failure_classification(component: str, error: ErrorInfo, expected: FailureClass) -> None:
    record = FailureClassifier.classify(
        "failure-classified", IDENTITY, error, NOW, component=component
    )

    assert record.classification is expected
    assert record.identity.correlation_id == "correlation-1"


def test_retry_is_bounded_and_exponential() -> None:
    policy = RecoveryPolicy(base_backoff_seconds=2, max_backoff_seconds=10, clock=lambda: NOW)

    first = policy.decide(request(failure(FailureClass.NETWORK_FAILURE)))
    exhausted = policy.decide(
        request(failure(FailureClass.TIMEOUT), attempt_count=3, max_attempts=3)
    )

    assert first.action is RecoveryAction.RETRY
    assert first.next_attempt == 2
    assert first.backoff_seconds == 4
    assert exhausted.action is RecoveryAction.STOP
    assert exhausted.reason_code == "recovery_attempts_exhausted"


def test_uncertain_side_effect_is_never_blindly_retried() -> None:
    policy = RecoveryPolicy(clock=lambda: NOW)
    uncertain = failure(
        FailureClass.EXTERNAL_SIDE_EFFECT_UNCERTAIN,
        side_effect=SideEffectState.UNCERTAIN,
    )

    no_check = policy.decide(request(uncertain, idempotent=False))
    with_check = policy.decide(request(uncertain, idempotent=False, reconciliation_available=True))

    assert no_check.action is RecoveryAction.MARK_UNCERTAIN
    assert no_check.requires_reconciliation
    assert with_check.action is RecoveryAction.RESUME
    assert with_check.requires_reconciliation
    assert all(item.action is not RecoveryAction.RETRY for item in (no_check, with_check))


def test_verification_failure_resumes_check_not_external_action() -> None:
    policy = RecoveryPolicy(clock=lambda: NOW)
    result = policy.decide(
        request(
            failure(
                FailureClass.VERIFICATION_FAILURE,
                side_effect=SideEffectState.CONFIRMED,
            ),
            idempotent=False,
            reconciliation_available=True,
        )
    )

    assert result.action is RecoveryAction.RESUME
    assert result.reason_code == "resume_verification"


def test_fallback_escalation_stop_and_resource_wait() -> None:
    policy = RecoveryPolicy(clock=lambda: NOW)

    fallback = policy.decide(
        request(
            failure(FailureClass.MODEL_FAILURE, retryable=False),
            fallback_available=True,
        )
    )
    escalation = policy.decide(request(failure(FailureClass.APPROVAL_FAILURE, retryable=False)))
    stopped = policy.decide(
        request(failure(FailureClass.RESOURCE_EXHAUSTION), hard_resource_limit=True)
    )
    waiting = policy.decide(
        request(failure(FailureClass.RESOURCE_EXHAUSTION), resource_available=False)
    )

    assert fallback.action is RecoveryAction.FALLBACK
    assert escalation.action is RecoveryAction.ESCALATE
    assert stopped.action is RecoveryAction.STOP
    assert waiting.action is RecoveryAction.WAIT


def test_recovery_task_adapter_uses_guarded_task_transition() -> None:
    task = Task("task-1", "request-1", "correlation-1", "Goal", "owner", max_attempts=2)
    task.assign_agent("agent-1")
    task.start()
    task.fail(ErrorInfo("failure", "Failed.", "Failure", retryable=True))
    decision = RecoveryPolicy(clock=lambda: NOW).decide(request(failure(FailureClass.TOOL_FAILURE)))

    assert TaskRecoveryAdapter.prepare_retry(task, decision)
    assert task.status is TaskStatus.RETRY
    assert task.attempt_count == 1


def operation(
    status: RecoverableOperationStatus,
    *,
    side_effect: SideEffectState = SideEffectState.NONE,
    max_attempts: int = 3,
) -> RecoverableOperation:
    return RecoverableOperation(
        f"operation-{status.value.lower()}",
        IDENTITY,
        status,
        1,
        max_attempts,
        True,
        side_effect,
        1,
        NOW,
    )


def test_startup_reconciliation_is_persistent_idempotent_and_correlated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.sqlite"
    store = SQLiteRecoveryStore(path)
    publisher = InProcessEventPublisher()
    store.register_operation(operation(RecoverableOperationStatus.RUNNING))
    store.register_operation(
        operation(
            RecoverableOperationStatus.RETRY_WAIT,
            side_effect=SideEffectState.UNCERTAIN,
        )
    )
    reconciler = StartupReconciler(
        store,
        RecoveryPolicy(clock=lambda: NOW),
        publisher=publisher,
        clock=lambda: NOW,
        id_factory=lambda: "recovery-event-1",
    )

    first = reconciler.reconcile()
    second = reconciler.reconcile()

    assert {item.action for item in first} == {
        RecoveryAction.RETRY,
        RecoveryAction.MARK_UNCERTAIN,
    }
    assert second[0].action is RecoveryAction.RETRY
    assert len(second) == 1  # terminal uncertain work no longer participates
    assert len(publisher.events()) == 2
    assert all(event.request_id == "request-1" for event in publisher.events())
    assert all(event.workflow_id == "workflow-1" for event in publisher.events())
    store.close()

    reopened = SQLiteRecoveryStore(path)
    running_decision = next(item for item in first if item.operation_id == "operation-running")
    assert reopened.decision_for("operation-running", 1) == running_decision
    assert reopened.operation("operation-retry_wait").status is RecoverableOperationStatus.UNCERTAIN  # type: ignore[union-attr]
    reopened.close()


def test_verifying_operation_resumes_verification_after_restart(tmp_path: Path) -> None:
    store = SQLiteRecoveryStore(tmp_path / "verify.sqlite")
    store.register_operation(operation(RecoverableOperationStatus.VERIFYING))

    decision = StartupReconciler(
        store,
        RecoveryPolicy(clock=lambda: NOW),
        clock=lambda: NOW,
    ).reconcile()[0]

    assert decision.action is RecoveryAction.RESUME
    assert decision.reason_code == "resume_verification"
    store.close()
