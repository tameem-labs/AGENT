from __future__ import annotations

from itertools import count
from pathlib import Path

from tests.unit.test_freelancing_outreach import NOW, TimeoutAdapter, preparation, system
from zyro.core.events import InProcessEventPublisher
from zyro.domains.freelancing.outreach import (
    OutreachRecoveryBridge,
    OutreachService,
    OutreachStatus,
    SQLiteOutreachStore,
)
from zyro.observability import OperationalObserver, SQLiteObservabilityStore, TraceQuery
from zyro.recovery import RecoveryAction, RecoveryPolicy, SQLiteRecoveryStore


def test_uncertain_external_action_is_durable_observable_and_never_resent(
    tmp_path: Path,
) -> None:
    adapter = TimeoutAdapter()
    base, approvals, _, store, resources, _, _ = system(tmp_path, adapter=adapter)
    trace_store = SQLiteObservabilityStore(tmp_path / "failure-traces.sqlite")
    trace_ids = count(1)
    observer = OperationalObserver(
        trace_store,
        clock=lambda: NOW,
        id_factory=lambda: f"failure-trace-{next(trace_ids)}",
    )
    publisher = InProcessEventPublisher()
    service = OutreachService(
        store,
        base._tools,
        publisher,
        observer=observer,
        clock=lambda: NOW,
        id_factory=iter(
            ("prepared-event", "pending-event", "uncertain-event", "repeat-event")
        ).__next__,
    )
    item = preparation()
    assert service.prepare(item)
    pending = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="outreach-instance",
    )
    approvals.approve(pending.approval_id or "", "human-1", "Exact action reviewed.")

    uncertain = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="outreach-instance",
        approval_id=pending.approval_id,
    )
    duplicate = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="outreach-instance",
        approval_id=pending.approval_id,
    )

    assert uncertain.status is duplicate.status is OutreachStatus.UNCERTAIN
    assert adapter.calls == 1
    assert store.action_required(item.external_action_id).attempts == 1
    assert not publisher.events("OUTREACH_ACCEPTED")
    assert not publisher.events("OUTREACH_DELIVERED")
    assert not publisher.events("OUTREACH_VERIFIED")
    assert len(publisher.events("OUTREACH_UNCERTAIN")) == 1

    recovery_store = SQLiteRecoveryStore(tmp_path / "failure-recovery.sqlite")
    bridge = OutreachRecoveryBridge(
        RecoveryPolicy(clock=lambda: NOW),
        store=recovery_store,
        observer=observer,
        clock=lambda: NOW,
        id_factory=iter(("failure-record", "recovery-decision")).__next__,
    )
    decision = bridge.decide(
        item,
        store.action_required(item.external_action_id),
        reconciliation_available=False,
    )
    repeated_decision = bridge.decide(
        item,
        store.action_required(item.external_action_id),
        reconciliation_available=False,
    )

    assert decision == repeated_decision
    assert decision.action is RecoveryAction.MARK_UNCERTAIN
    assert recovery_store.operation(item.external_action_id) is not None
    assert recovery_store.decision_for(item.external_action_id, 1) == decision
    traces = trace_store.query(TraceQuery(correlation_id=item.correlation_id))
    assert {trace.event_type for trace in traces} >= {
        "OUTREACH_PREPARED",
        "OUTREACH_APPROVAL_REQUIRED",
        "OUTREACH_UNCERTAIN",
        "OUTREACH_RECOVERY_DECIDED",
    }
    recovery_trace = next(
        trace for trace in traces if trace.event_type == "OUTREACH_RECOVERY_DECIDED"
    )
    assert recovery_trace.recovery_id == decision.recovery_id
    assert recovery_trace.metadata["external_action_id"] == item.external_action_id

    store.close()
    reopened = SQLiteOutreachStore(tmp_path / "outreach.sqlite", clock=lambda: NOW)
    assert reopened.action_required(item.external_action_id).status is OutreachStatus.UNCERTAIN
    assert reopened.reconciled_uncertain == 0
    reopened.close()
    resources.close()
    recovery_store.close()
    trace_store.close()
