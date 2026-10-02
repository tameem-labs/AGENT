"""Unit tests for Attention and Proactive Intelligence subsystem."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from zyro.core.risk import RiskClass
from zyro.intelligence import (
    AttentionCategory,
    AttentionPriority,
    AttentionService,
)
from zyro.security.approval import ApprovalAction, ApprovalService, digest_arguments
from zyro.security.permission import PermissionScope
from zyro.workflows import (
    WorkflowStore,
)


def test_attention_service_detects_expiring_approvals_and_failed_workflows(
    tmp_path: Path,
) -> None:
    approvals = ApprovalService(frozenset({"owner-1"}))
    workflows = WorkflowStore(tmp_path / "test_wf.sqlite")
    now = datetime.now(UTC)

    try:
        attention = AttentionService(approvals, workflows)
        # Empty initially
        items_empty = attention.scan()
        assert len(items_empty) == 0

        # Create an expiring approval request (expires in 10 minutes)
        act = ApprovalAction(
            executor_id="agent-1",
            action="gmail.send",
            capability="google.gmail_send",
            target="client@example.com",
            scope=PermissionScope(tool_id="gmail", target="client@example.com", action="send"),
            risk_class=RiskClass.STRICT_AUTHORIZATION,
            reason="Dispatch freelance outreach",
            expected_effect="Email is sent to client",
            arguments_digest=digest_arguments({"subject": "Proposal"}),
            conditions={"environment": "test"},
        )
        req = approvals.create_request(
            request_id="req-1",
            task_id="task-1",
            workflow_id="wf-1",
            requester_id="zyro.freelancing",
            action=act,
            policy_version="policy-v1",
            expires_at=now + timedelta(minutes=10),
        )

        items = attention.scan()
        assert len(items) == 1
        assert items[0].category is AttentionCategory.APPROVAL_EXPIRING
        assert items[0].priority is AttentionPriority.HIGH
        assert items[0].reference_id == req.approval_id

        # Acknowledge the attention item
        attention.acknowledge(items[0].attention_id)
        scanned_after_ack = attention.scan()
        assert scanned_after_ack[0].acknowledged is True
    finally:
        workflows.close()
