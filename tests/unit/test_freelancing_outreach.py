from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from zyro.core.errors import ErrorInfo
from zyro.core.events import InProcessEventPublisher
from zyro.core.risk import RiskClass
from zyro.domains.freelancing.outreach import (
    OUTREACH_TOOL_ID,
    DeterministicSimulatedChannel,
    ExternalDispatchResult,
    OutreachChannel,
    OutreachPreparation,
    OutreachRecoveryBridge,
    OutreachService,
    OutreachStatus,
    OutreachToolHandler,
    SQLiteOutreachStore,
    outreach_tool_definition,
)
from zyro.recovery import RecoveryAction, RecoveryPolicy
from zyro.resources import RateLimitPolicy, ResourcePolicy, SQLiteResourceManager
from zyro.security.approval import ApprovalService, ApprovalState
from zyro.security.authorization import ToolAuthorizationService
from zyro.security.permission import (
    Permission,
    PermissionEvaluator,
    PermissionScope,
    PermissionStatus,
    PermissionStore,
    PrincipalDirectory,
)
from zyro.security.policy import RiskPolicy
from zyro.tools.contracts import ToolCall, ToolResultStatus
from zyro.tools.executor import ToolExecutor
from zyro.tools.registry import ToolRegistry

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class UncertainAdapter:
    adapter_id = "uncertain-adapter"

    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, preparation: OutreachPreparation) -> ExternalDispatchResult:
        self.calls += 1
        return ExternalDispatchResult(
            OutreachStatus.UNCERTAIN,
            error=ErrorInfo(
                "external_outcome_uncertain",
                "The external outcome could not be established.",
                "ExternalSideEffectUncertainty",
            ),
        )


class DeliveredAdapter:
    adapter_id = "delivered-adapter"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def dispatch(self, preparation: OutreachPreparation) -> ExternalDispatchResult:
        self.calls.append(preparation.external_action_id)
        return ExternalDispatchResult(
            OutreachStatus.DELIVERED,
            provider_reference="provider-reference-1",
            delivery_confirmed=True,
            verification_reference="provider-receipt-1",
        )


class TimeoutAdapter:
    adapter_id = "timeout-adapter"

    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, preparation: OutreachPreparation) -> ExternalDispatchResult:
        self.calls += 1
        raise TimeoutError("provider detail")


def preparation(**changes: Any) -> OutreachPreparation:
    values: dict[str, Any] = {
        "preparation_id": "preparation-1",
        "external_action_id": "action-1",
        "lead_id": "lead-1",
        "lead_revision": 3,
        "qualification_verification_id": "verification-qualified-1",
        "recipient_id": "client-1",
        "recipient": "client@example.test",
        "objective": "Offer a bounded implementation review.",
        "channel": OutreachChannel.EMAIL,
        "subject": "Implementation review",
        "message_body": "Hello, I can help review the described implementation.",
        "request_id": "request-1",
        "task_id": "task-1",
        "correlation_id": "correlation-1",
        "workflow_id": "workflow-1",
        "agent_id": "outreach-agent",
        "policy_version": "policy-v1",
        "verification_method": "provider delivery receipt",
        "personalization_evidence": ("evidence-1",),
        "source_references": ("source-1",),
        "created_at": NOW,
    }
    values.update(changes)
    return OutreachPreparation(**values)


def system(
    tmp_path: Path,
    *,
    adapter: (
        DeterministicSimulatedChannel | DeliveredAdapter | UncertainAdapter | TimeoutAdapter | None
    ) = None,
    clock: Clock | None = None,
    rate_limit: bool = False,
) -> tuple[
    OutreachService,
    ApprovalService,
    PermissionStore,
    SQLiteOutreachStore,
    SQLiteResourceManager,
    DeterministicSimulatedChannel | DeliveredAdapter | UncertainAdapter | TimeoutAdapter,
    InProcessEventPublisher,
]:
    actual_clock = clock or Clock()
    permissions = PermissionStore()
    permissions.add(
        Permission(
            "permission-send",
            "outreach-agent",
            "send_outreach",
            PermissionScope(tool_id=OUTREACH_TOOL_ID, action="execute"),
            "policy-v1",
            issued_at=NOW - timedelta(minutes=1),
        )
    )
    evaluator = PermissionEvaluator(
        permissions,
        PrincipalDirectory(("outreach-agent", "owner-1", "human-1")),
        frozenset({"send_outreach"}),
        policy_version="policy-v1",
        clock=actual_clock,
        id_factory=lambda: "permission-decision-1",
    )
    approval_ids = iter(("approval-1", "approval-2", "approval-3"))
    approvals = ApprovalService(
        frozenset({"human-1"}),
        clock=actual_clock,
        id_factory=lambda: next(approval_ids),
    )
    authorizer = ToolAuthorizationService(
        evaluator,
        RiskPolicy(policy_version="policy-v1"),
        approvals,
        approval_ttl=timedelta(minutes=5),
        clock=actual_clock,
    )
    resources = SQLiteResourceManager(
        tmp_path / "resources.sqlite",
        ResourcePolicy(
            max_concurrent_tool_calls=1,
            rate_limits={"email-provider": RateLimitPolicy(1, 60)} if rate_limit else {},
        ),
        clock=actual_clock,
    )
    store = SQLiteOutreachStore(tmp_path / "outreach.sqlite", clock=actual_clock)
    actual_adapter = adapter or DeterministicSimulatedChannel()
    handler = OutreachToolHandler(
        store,
        {OutreachChannel.EMAIL: actual_adapter},
        resources,
        rate_resource_ids={OutreachChannel.EMAIL: "email-provider"} if rate_limit else {},
    )
    registry = ToolRegistry()
    registry.register(outreach_tool_definition(), handler)
    publisher = InProcessEventPublisher()
    event_ids = count(1)
    service = OutreachService(
        store,
        ToolExecutor(registry, authorizer),
        publisher,
        clock=actual_clock,
        id_factory=lambda: f"event-{next(event_ids)}",
    )
    return service, approvals, permissions, store, resources, actual_adapter, publisher


def test_outreach_preparation_is_strict_exact_and_secret_safe() -> None:
    item = preparation()

    assert item.risk_class is RiskClass.STRICT_AUTHORIZATION
    assert item.approval_required
    assert item.approval_display["exact_final_message"] == item.message_body
    assert item.payload_digest

    with pytest.raises(ValueError, match="secret-shaped"):
        preparation(message_body="password=should-not-be-here")
    with pytest.raises(ValueError, match="strict"):
        preparation(risk_class=RiskClass.AUTOMATIC)


def test_qualified_or_prepared_work_cannot_send_without_exact_human_approval(
    tmp_path: Path,
) -> None:
    service, approvals, _, store, resources, adapter, publisher = system(tmp_path)
    item = preparation()
    assert service.prepare(item)

    pending = service.dispatch(
        item.preparation_id, requester_id="owner-1", instance_id="instance-1"
    )

    assert pending.status is OutreachStatus.APPROVAL_REQUIRED
    assert pending.approval_id == "approval-1"
    approval = approvals.get("approval-1")
    assert approval.state is ApprovalState.PENDING
    assert approval.display["exact_final_message"] == item.message_body
    assert approval.display["recipient"] == item.recipient
    assert adapter.calls == []
    assert store.action(item.external_action_id) is None
    assert [event.event_type for event in publisher.events()] == [
        "OUTREACH_PREPARED",
        "OUTREACH_APPROVAL_REQUIRED",
    ]
    resources.close()
    store.close()


def test_approved_exact_message_dispatches_once_and_simulation_never_verifies(
    tmp_path: Path,
) -> None:
    service, approvals, _, store, resources, adapter, _ = system(tmp_path)
    item = preparation()
    service.prepare(item)
    pending = service.dispatch(item.preparation_id, requester_id="owner-1", instance_id="i-1")
    approvals.approve(pending.approval_id or "", "human-1", "Exact content reviewed.")

    first = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="i-1",
        approval_id=pending.approval_id,
    )
    duplicate = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="i-1",
        approval_id=pending.approval_id,
    )
    verified = service.verify_delivery(item.external_action_id, verification_reference="receipt")

    assert first.status is OutreachStatus.ACCEPTED
    assert duplicate.status is OutreachStatus.ACCEPTED
    assert adapter.calls == ["action-1"]
    assert verified.status is OutreachStatus.SUCCEEDED_BUT_UNVERIFIED
    assert verified.simulated
    resources.close()
    store.close()


def test_real_adapter_delivery_remains_distinct_until_separately_verified(tmp_path: Path) -> None:
    adapter = DeliveredAdapter()
    service, approvals, _, store, resources, _, _ = system(tmp_path, adapter=adapter)
    item = preparation()
    service.prepare(item)
    pending = service.dispatch(item.preparation_id, requester_id="owner-1", instance_id="i-1")
    approvals.approve(pending.approval_id or "", "human-1", "Reviewed.")

    delivered = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="i-1",
        approval_id=pending.approval_id,
    )
    verified = service.verify_delivery(
        item.external_action_id, verification_reference="independent-receipt-1"
    )

    assert delivered.status is OutreachStatus.DELIVERED
    assert verified.status is OutreachStatus.VERIFIED
    assert store.action_required(item.external_action_id).status is OutreachStatus.VERIFIED
    resources.close()
    store.close()


def test_model_or_executor_cannot_approve_its_own_outreach(tmp_path: Path) -> None:
    service, approvals, _, store, resources, adapter, _ = system(tmp_path)
    item = preparation()
    service.prepare(item)
    pending = service.dispatch(item.preparation_id, requester_id="owner-1", instance_id="i-1")

    with pytest.raises(ValueError, match="not authorized"):
        approvals.approve(pending.approval_id or "", item.agent_id, "Model recommended sending.")
    assert adapter.calls == []
    resources.close()
    store.close()


def test_materially_changed_message_invalidates_approval(tmp_path: Path) -> None:
    service, approvals, _, store, resources, adapter, _ = system(tmp_path)
    item = preparation()
    service.prepare(item)
    pending = service.dispatch(item.preparation_id, requester_id="owner-1", instance_id="i-1")
    approvals.approve("approval-1", "human-1", "Reviewed.")

    changed = dict(item.execution_arguments)
    changed["message_body"] = "Materially changed final message."
    executor = service._tools  # exercise the same canonical ToolExecutor boundary
    result = executor.execute(
        ToolCall(
            OUTREACH_TOOL_ID,
            changed,
            item.request_id,
            item.task_id,
            item.agent_id,
            "i-1",
            item.correlation_id,
            workflow_id=item.workflow_id,
            requester_id="owner-1",
            capability="send_outreach",
            target=f"EMAIL:{item.recipient}",
            purpose=item.objective,
            expected_effect="Send the exact approved message to the named recipient.",
            approval_id=pending.approval_id,
            conditions={"lead_id": item.lead_id},
            approval_context={"exact_final_message": changed["message_body"]},
        )
    )

    assert result.status is ToolResultStatus.APPROVAL_INVALID
    assert adapter.calls == []
    resources.close()
    store.close()


def test_expired_approval_and_revoked_permission_both_block_send(tmp_path: Path) -> None:
    clock = Clock()
    service, approvals, permissions, store, resources, adapter, _ = system(tmp_path, clock=clock)
    item = preparation()
    service.prepare(item)
    pending = service.dispatch(item.preparation_id, requester_id="owner-1", instance_id="i-1")
    approvals.approve("approval-1", "human-1", "Reviewed.")
    clock.now += timedelta(minutes=6)

    expired = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="i-1",
        approval_id=pending.approval_id,
    )
    assert expired.status is OutreachStatus.REJECTED
    permissions.set_status("permission-send", PermissionStatus.REVOKED)
    revoked = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="i-1",
        approval_id=pending.approval_id,
    )

    assert revoked.status is OutreachStatus.REJECTED
    assert adapter.calls == []
    resources.close()
    store.close()


@pytest.mark.parametrize("adapter_type", [UncertainAdapter, TimeoutAdapter])
def test_uncertain_dispatch_is_durable_and_never_blindly_repeated(
    tmp_path: Path,
    adapter_type: type[UncertainAdapter] | type[TimeoutAdapter],
) -> None:
    adapter = adapter_type()
    service, approvals, _, store, resources, _, _ = system(tmp_path, adapter=adapter)
    item = preparation()
    service.prepare(item)
    pending = service.dispatch(item.preparation_id, requester_id="owner-1", instance_id="i-1")
    approvals.approve("approval-1", "human-1", "Reviewed.")

    first = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="i-1",
        approval_id=pending.approval_id,
    )
    second = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="i-1",
        approval_id=pending.approval_id,
    )

    assert first.status is OutreachStatus.UNCERTAIN
    assert second.status is OutreachStatus.UNCERTAIN
    assert adapter.calls == 1
    decision = OutreachRecoveryBridge(
        RecoveryPolicy(clock=lambda: NOW),
        clock=lambda: NOW,
        id_factory=iter(("failure-1", "recovery-1")).__next__,
    ).decide(
        item,
        store.action_required(item.external_action_id),
        reconciliation_available=False,
    )
    assert decision.action is RecoveryAction.MARK_UNCERTAIN
    store.close()
    reopened = SQLiteOutreachStore(tmp_path / "outreach.sqlite")
    assert reopened.action_required("action-1").status is OutreachStatus.UNCERTAIN
    reopened.close()
    resources.close()


def test_provider_rate_limit_blocks_second_action_without_bypass(tmp_path: Path) -> None:
    service, approvals, _, store, resources, adapter, _ = system(tmp_path, rate_limit=True)
    first = preparation()
    service.prepare(first)
    pending_first = service.dispatch(
        first.preparation_id, requester_id="owner-1", instance_id="i-1"
    )
    approvals.approve(pending_first.approval_id or "", "human-1", "Reviewed first action.")
    assert (
        service.dispatch(
            first.preparation_id,
            requester_id="owner-1",
            instance_id="i-1",
            approval_id=pending_first.approval_id,
        ).status
        is OutreachStatus.ACCEPTED
    )

    second = preparation(preparation_id="preparation-2", external_action_id="action-2")
    service.prepare(second)
    pending_second = service.dispatch(
        second.preparation_id, requester_id="owner-1", instance_id="i-2"
    )
    approvals.approve(pending_second.approval_id or "", "human-1", "Reviewed second action.")
    blocked = service.dispatch(
        second.preparation_id,
        requester_id="owner-1",
        instance_id="i-2",
        approval_id=pending_second.approval_id,
    )

    assert blocked.status is OutreachStatus.FAILED
    assert store.action("action-2") is None
    assert adapter.calls == ["action-1"]
    resources.close()
    store.close()


def test_restart_marks_interrupted_dispatch_uncertain_without_adapter_call(tmp_path: Path) -> None:
    path = tmp_path / "outreach.sqlite"
    store = SQLiteOutreachStore(path, clock=lambda: NOW)
    item = preparation()
    store.save_preparation(item)
    should_dispatch, _ = store.begin_dispatch(item)
    assert should_dispatch
    store.close()

    reopened = SQLiteOutreachStore(path, clock=lambda: NOW + timedelta(seconds=1))

    assert reopened.reconciled_uncertain == 1
    assert reopened.action_required("action-1").status is OutreachStatus.UNCERTAIN
    reopened.close()
