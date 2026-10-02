from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_freelancing_outreach import DeliveredAdapter, preparation, system
from zyro.core.errors import InvalidTaskError
from zyro.core.events import Event, EventDelivery
from zyro.core.task import Task
from zyro.domains.freelancing.outreach import (
    OUTREACH_TOOL_ID,
    OutreachStatus,
    OutreachStoreError,
)
from zyro.security.approval import ApprovalError
from zyro.tools.contracts import ToolCall, ToolResultStatus


def _approved(tmp_path: Path, *, adapter: DeliveredAdapter | None = None) -> tuple[Any, ...]:
    service, approvals, permissions, store, resources, actual_adapter, publisher = system(
        tmp_path, adapter=adapter
    )
    item = preparation()
    service.prepare(item)
    pending = service.dispatch(
        item.preparation_id, requester_id="owner-1", instance_id="instance-1"
    )
    approvals.approve(pending.approval_id or "", "human-1", "Exact action reviewed.")
    return (
        service,
        approvals,
        permissions,
        store,
        resources,
        actual_adapter,
        publisher,
        item,
        pending,
    )


def _call(item: Any, arguments: dict[str, Any], approval_id: str, **changes: Any) -> ToolCall:
    values: dict[str, Any] = {
        "tool_id": OUTREACH_TOOL_ID,
        "arguments": arguments,
        "request_id": item.request_id,
        "task_id": item.task_id,
        "workflow_id": item.workflow_id,
        "agent_id": item.agent_id,
        "instance_id": "instance-1",
        "correlation_id": item.correlation_id,
        "requester_id": "owner-1",
        "capability": "send_outreach",
        "target": f"{item.channel.value}:{item.recipient}",
        "purpose": item.objective,
        "expected_effect": "Send the exact approved message to the named recipient.",
        "approval_id": approval_id,
        "conditions": {"lead_id": item.lead_id},
        "approval_context": item.approval_display,
    }
    values.update(changes)
    return ToolCall(**values)


@pytest.mark.parametrize(
    ("argument_name", "replacement"),
    [
        ("recipient", "different@example.test"),
        ("recipient_id", "different-client"),
        ("channel", "CRM"),
        ("external_action_id", "different-action"),
        ("lead_revision", 999),
    ],
)
def test_approved_outreach_rejects_every_changed_execution_dimension(
    tmp_path: Path,
    argument_name: str,
    replacement: object,
) -> None:
    service, _, _, store, resources, adapter, _, item, pending = _approved(tmp_path)
    changed = dict(item.execution_arguments)
    changed[argument_name] = replacement

    result = service._tools.execute(_call(item, changed, pending.approval_id or ""))

    assert result.status is ToolResultStatus.APPROVAL_INVALID
    assert adapter.calls == []
    assert store.action(item.external_action_id) is None
    resources.close()
    store.close()


def test_approval_is_bound_to_requester_request_task_and_workflow(tmp_path: Path) -> None:
    service, _, _, store, resources, adapter, _, item, pending = _approved(tmp_path)

    for changed in (
        {"requester_id": "different-requester"},
        {"request_id": "different-request"},
        {"task_id": "different-task"},
        {"workflow_id": "different-workflow"},
    ):
        result = service._tools.execute(
            _call(item, dict(item.execution_arguments), pending.approval_id or "", **changed)
        )
        assert result.status is ToolResultStatus.APPROVAL_INVALID

    wrong_executor = service._tools.execute(
        _call(
            item,
            dict(item.execution_arguments),
            pending.approval_id or "",
            agent_id="different-executor",
        )
    )
    assert wrong_executor.status is ToolResultStatus.PERMISSION_DENIED
    assert adapter.calls == []
    assert store.action(item.external_action_id) is None
    resources.close()
    store.close()


def test_cancelled_and_denied_approval_never_dispatch(tmp_path: Path) -> None:
    service, approvals, _, store, resources, adapter, _, item, pending = _approved(tmp_path)
    # A final approval cannot be rewritten to cancellation or denial.
    with pytest.raises(ApprovalError, match="cannot transition"):
        approvals.cancel(pending.approval_id or "", "owner-1", "cancel")

    second = replace(item, preparation_id="preparation-2", external_action_id="action-2")
    service.prepare(second)
    second_pending = service.dispatch(
        second.preparation_id, requester_id="owner-1", instance_id="instance-2"
    )
    approvals.deny(second_pending.approval_id or "", "human-1", "Do not send.")
    denied = service.dispatch(
        second.preparation_id,
        requester_id="owner-1",
        instance_id="instance-2",
        approval_id=second_pending.approval_id,
    )

    third = replace(item, preparation_id="preparation-3", external_action_id="action-3")
    service.prepare(third)
    third_pending = service.dispatch(
        third.preparation_id, requester_id="owner-1", instance_id="instance-3"
    )
    approvals.cancel(third_pending.approval_id or "", "owner-1", "No longer wanted.")
    cancelled = service.dispatch(
        third.preparation_id,
        requester_id="owner-1",
        instance_id="instance-3",
        approval_id=third_pending.approval_id,
    )

    assert denied.status is OutreachStatus.REJECTED
    assert cancelled.status is OutreachStatus.REJECTED
    assert adapter.calls == []
    assert store.action("action-2") is None
    assert store.action("action-3") is None
    resources.close()
    store.close()


def test_identical_approval_callback_is_idempotent_but_conflict_is_rejected(
    tmp_path: Path,
) -> None:
    _, approvals, _, store, resources, _, _, _, pending = _approved(tmp_path)
    approval_id = pending.approval_id or ""

    repeated = approvals.approve(approval_id, "human-1", "Exact action reviewed.")

    assert repeated == approvals.get(approval_id)
    assert len(approvals.history(approval_id)) == 1
    with pytest.raises(ApprovalError, match="conflicting final decision"):
        approvals.approve(approval_id, "human-1", "A different decision record.")
    resources.close()
    store.close()


def test_delivery_verification_is_idempotent_and_conflicting_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    adapter = DeliveredAdapter()
    service, _, _, store, resources, _, _, item, pending = _approved(tmp_path, adapter=adapter)
    dispatched = service.dispatch(
        item.preparation_id,
        requester_id="owner-1",
        instance_id="instance-1",
        approval_id=pending.approval_id,
    )
    assert dispatched.status is OutreachStatus.DELIVERED

    first = service.verify_delivery(
        item.external_action_id, verification_reference="independent-evidence-1"
    )
    repeated = service.verify_delivery(
        item.external_action_id, verification_reference="independent-evidence-1"
    )

    assert first.status is repeated.status is OutreachStatus.VERIFIED
    with pytest.raises(OutreachStoreError, match="conflicting verification evidence"):
        service.verify_delivery(
            item.external_action_id, verification_reference="different-evidence"
        )
    assert adapter.calls == [item.external_action_id]
    resources.close()
    store.close()


def test_task_contract_rejects_secret_shaped_and_unbounded_goal_payloads() -> None:
    def task(goal: str) -> Task:
        return Task(
            "task-hostile",
            "request-hostile",
            "correlation-hostile",
            goal,
            "owner-hostile",
        )

    with pytest.raises(InvalidTaskError, match="secret-shaped"):
        task("Execute work with password=not-allowed")
    with pytest.raises(InvalidTaskError, match="bounded size"):
        task("x" * 20_000)


def test_event_payload_rejects_nested_secret_assignments_and_oversized_content() -> None:
    def event(payload: dict[str, Any]) -> Event:
        return Event(
            "event-hostile",
            "request-hostile",
            "task-hostile",
            "correlation-hostile",
            "HOSTILE_INPUT",
            "security-test",
            payload,
            preparation().created_at,
            "1.0",
            delivery=EventDelivery(durable=True),
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        event({"nested": [{"text": "access_token=not-allowed"}]})
    with pytest.raises(ValueError, match="bounded envelope"):
        event({"text": "x" * 70_000})
