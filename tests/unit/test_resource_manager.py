from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from zyro.core.config import AppConfig
from zyro.core.events import InProcessEventPublisher
from zyro.recovery import (
    FailureClass,
    FailureIdentity,
    RecoveryAction,
    RecoveryPolicy,
    RecoveryRequest,
)
from zyro.resources import (
    DEFAULT_TASK_TOKEN_LIMIT,
    DEFAULT_WORKFLOW_TOKEN_LIMIT,
    AdmissionOutcome,
    ConsumptionOutcome,
    RateLimitPolicy,
    ReservationStatus,
    ResourceKind,
    ResourcePolicy,
    ResourceRecoveryBridge,
    SQLiteResourceManager,
    UsagePrecision,
    WorkLane,
    resource_policy_from_config,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_default_and_configured_resource_policy_values() -> None:
    defaults = ResourcePolicy()
    configured = resource_policy_from_config(
        AppConfig(
            task_token_limit=100,
            workflow_token_limit=500,
            max_concurrent_tasks=2,
            max_concurrent_agents=3,
            max_concurrent_tool_calls=1,
        )
    )

    assert defaults.task_token_limit == DEFAULT_TASK_TOKEN_LIMIT == 50_000
    assert defaults.workflow_token_limit == DEFAULT_WORKFLOW_TOKEN_LIMIT == 300_000
    assert configured.task_token_limit == 100
    assert configured.workflow_token_limit == 500
    assert configured.max_concurrent_agents == 3


def test_token_accounting_hard_stop_and_restart_persistence(tmp_path: Path) -> None:
    path = tmp_path / "resources.sqlite"
    policy = ResourcePolicy(task_token_limit=10, workflow_token_limit=15)
    manager = SQLiteResourceManager(path, policy, clock=lambda: NOW)

    accepted = manager.consume_tokens(
        "task-1", "workflow-1", 8, UsagePrecision.EXACT, agent_id="agent-1", consumer_id="model-1"
    )
    stopped = manager.consume_tokens("task-1", "workflow-1", 3, UsagePrecision.EXACT)

    assert accepted.outcome is ConsumptionOutcome.ACCEPTED
    assert accepted.task_used == 8 and accepted.remaining_task == 2
    assert stopped.outcome is ConsumptionOutcome.RESOURCE_LIMIT_REACHED
    assert stopped.task_used == 8  # rejected use is not silently consumed or reset
    assert manager.hard_stop_count("task-1") == 1
    manager.close()

    reopened = SQLiteResourceManager(path, policy, clock=lambda: NOW)
    still_stopped = reopened.consume_tokens("task-1", "workflow-1", 3, UsagePrecision.ESTIMATED)
    assert still_stopped.outcome is ConsumptionOutcome.RESOURCE_LIMIT_REACHED
    assert still_stopped.task_used == 8
    assert reopened.hard_stop_count("task-1") == 2
    reopened.close()


def test_workflow_limit_is_independent_across_tasks(tmp_path: Path) -> None:
    manager = SQLiteResourceManager(
        tmp_path / "workflow.sqlite",
        ResourcePolicy(task_token_limit=20, workflow_token_limit=10),
        clock=lambda: NOW,
    )
    manager.consume_tokens("task-1", "workflow-1", 6, UsagePrecision.EXACT)

    result = manager.consume_tokens("task-2", "workflow-1", 5, UsagePrecision.EXACT)

    assert result.outcome is ConsumptionOutcome.RESOURCE_LIMIT_REACHED
    assert result.task_used == 0
    assert result.workflow_used == 6
    manager.close()


def test_unknown_usage_is_recorded_without_fake_precision(tmp_path: Path) -> None:
    manager = SQLiteResourceManager(tmp_path / "unknown.sqlite", clock=lambda: NOW)

    result = manager.consume_tokens(
        "task-1", None, None, UsagePrecision.UNKNOWN, consumer_id="provider-unknown"
    )

    assert result.outcome is ConsumptionOutcome.UNKNOWN_RECORDED
    assert result.units is None
    assert result.task_used == 0
    manager.close()


def test_concurrency_queue_interactive_priority_and_background_fairness(tmp_path: Path) -> None:
    clock = Clock()
    manager = SQLiteResourceManager(
        tmp_path / "queue.sqlite",
        ResourcePolicy(max_concurrent_agents=1, background_fairness_seconds=5),
        clock=clock,
    )
    active = manager.reserve(
        "reservation-active",
        "owner-active",
        ResourceKind.AGENT_SLOT,
        "agents",
        WorkLane.BACKGROUND,
        "task-active",
    )
    background = manager.reserve(
        "reservation-background",
        "owner-background",
        ResourceKind.AGENT_SLOT,
        "agents",
        WorkLane.BACKGROUND,
        "task-background",
    )
    interactive = manager.reserve(
        "reservation-interactive",
        "owner-interactive",
        ResourceKind.AGENT_SLOT,
        "agents",
        WorkLane.INTERACTIVE,
        "task-interactive",
    )

    assert active.outcome is AdmissionOutcome.ADMITTED
    assert background.outcome is AdmissionOutcome.QUEUED
    assert interactive.outcome is AdmissionOutcome.QUEUED
    manager.release("reservation-active", "owner-active")
    assert manager.reservation("reservation-interactive").status is ReservationStatus.ACTIVE
    assert manager.reservation("reservation-background").status is ReservationStatus.QUEUED

    clock.advance(6)
    manager.release("reservation-interactive", "owner-interactive")
    assert manager.reservation("reservation-background").status is ReservationStatus.ACTIVE
    manager.close()


def test_terminal_reservation_id_is_not_silently_revived_or_reported_queued(
    tmp_path: Path,
) -> None:
    manager = SQLiteResourceManager(tmp_path / "terminal.sqlite", clock=lambda: NOW)
    manager.reserve(
        "reservation-1",
        "owner-1",
        ResourceKind.TASK_SLOT,
        "tasks",
        WorkLane.INTERACTIVE,
        "task-1",
    )
    manager.release("reservation-1", "owner-1")

    repeated = manager.reserve(
        "reservation-1",
        "owner-1",
        ResourceKind.TASK_SLOT,
        "tasks",
        WorkLane.INTERACTIVE,
        "task-1",
    )

    assert repeated.outcome is AdmissionOutcome.LIMIT_REACHED
    assert repeated.reservation.status is ReservationStatus.RELEASED
    manager.close()


def test_lease_expiry_releases_capacity_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "leases.sqlite"
    clock = Clock()
    policy = ResourcePolicy(max_concurrent_tool_calls=1, default_lease_seconds=5)
    manager = SQLiteResourceManager(path, policy, clock=clock)
    manager.reserve(
        "tool-active",
        "owner-1",
        ResourceKind.TOOL_CALL,
        "tool-1",
        WorkLane.BACKGROUND,
        "task-1",
    )
    manager.reserve(
        "tool-queued",
        "owner-2",
        ResourceKind.TOOL_CALL,
        "tool-1",
        WorkLane.INTERACTIVE,
        "task-2",
    )
    manager.close()
    clock.advance(6)

    reopened = SQLiteResourceManager(path, policy, clock=clock)

    assert reopened.reservation("tool-active").status is ReservationStatus.EXPIRED
    assert reopened.reservation("tool-queued").status is ReservationStatus.ACTIVE
    reopened.close()


def test_rate_limit_retry_after_and_idempotent_request(tmp_path: Path) -> None:
    clock = Clock()
    manager = SQLiteResourceManager(
        tmp_path / "rate.sqlite",
        ResourcePolicy(rate_limits={"provider-1": RateLimitPolicy(2, 10)}),
        clock=clock,
    )

    first = manager.check_rate_limit("call-1", "provider-1")
    duplicate = manager.check_rate_limit("call-1", "provider-1")
    second = manager.check_rate_limit("call-2", "provider-1")
    blocked = manager.check_rate_limit("call-3", "provider-1")

    assert first.allowed and duplicate.allowed and second.allowed
    assert duplicate.used == 1
    assert not blocked.allowed and blocked.retry_after_seconds == 10
    clock.advance(10.1)
    assert manager.check_rate_limit("call-3", "provider-1").allowed
    manager.close()


def test_resource_exhaustion_integrates_with_recovery_without_bypass(tmp_path: Path) -> None:
    publisher = InProcessEventPublisher()
    identities = iter(("resource-failure", "resource-event"))
    manager = SQLiteResourceManager(
        tmp_path / "bridge.sqlite", ResourcePolicy(task_token_limit=1), clock=lambda: NOW
    )
    result = manager.consume_tokens("task-1", None, 2, UsagePrecision.EXACT)
    identity = FailureIdentity("request-1", "task-1", "correlation-1")
    failure = ResourceRecoveryBridge(
        clock=lambda: NOW,
        id_factory=lambda: next(identities),
        publisher=publisher,
    ).failure(result, identity)

    assert failure is not None and failure.classification is FailureClass.RESOURCE_EXHAUSTION
    event = publisher.events("RESOURCE_LIMIT_REACHED")[0]
    assert event.request_id == "request-1"
    assert event.task_id == "task-1"
    assert event.correlation_id == "correlation-1"
    assert event.payload["task_limit"] == 1
    decision = RecoveryPolicy(clock=lambda: NOW).decide(
        RecoveryRequest(
            "recovery-1",
            "operation-1",
            failure,
            1,
            3,
            True,
            True,
            True,
            False,
            False,
            "RUNNING",
        )
    )
    assert decision.action is RecoveryAction.STOP
    assert manager.hard_stop_count("task-1") == 1
    manager.close()
