"""Attention and Proactive Intelligence subsystem for ZYRO."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from zyro.core.data import validate_text
from zyro.security.approval import ApprovalService, ApprovalState
from zyro.workflows.contracts import WorkflowStatus
from zyro.workflows.store import WorkflowStore


class AttentionCategory(StrEnum):
    APPROVAL_EXPIRING = "APPROVAL_EXPIRING"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_BLOCKED = "WORKFLOW_BLOCKED"
    TASK_FAILED = "TASK_FAILED"
    INTEGRATION_ATTENTION = "INTEGRATION_ATTENTION"
    SYSTEM_ALERT = "SYSTEM_ALERT"


class AttentionPriority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AttentionItem:
    attention_id: str
    category: AttentionCategory
    priority: AttentionPriority
    title: str
    reason: str
    action_hint: str | None = None
    reference_id: str | None = None
    created_at: datetime = field(default_factory=_now)
    acknowledged: bool = False

    def __post_init__(self) -> None:
        for name in ("attention_id", "title", "reason"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "attention_id": self.attention_id,
            "category": self.category.value,
            "priority": self.priority.value,
            "title": self.title,
            "reason": self.reason,
            "action_hint": self.action_hint,
            "reference_id": self.reference_id,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "acknowledged": self.acknowledged,
        }


class AttentionService:
    """Surfaces actionable runtime events to user without unauthorized actions."""

    def __init__(
        self,
        approvals: ApprovalService,
        workflows: WorkflowStore,
    ) -> None:
        self._approvals = approvals
        self._workflows = workflows
        self._acknowledged_ids: set[str] = set()

    def scan(self) -> tuple[AttentionItem, ...]:
        items: list[AttentionItem] = []
        now = datetime.now(UTC)

        # 1. Expiring approvals
        for req in self._approvals.requests():
            if req.state is ApprovalState.PENDING:
                time_left = req.expires_at - now
                if timedelta(0) < time_left < timedelta(minutes=15):
                    mins = int(time_left.total_seconds() / 60)
                    reason_msg = (
                        f"Approval request for {req.action.target} expires in {mins} minutes."
                    )
                    aid = f"att-app-{req.approval_id}"
                    items.append(
                        AttentionItem(
                            attention_id=aid,
                            category=AttentionCategory.APPROVAL_EXPIRING,
                            priority=AttentionPriority.HIGH,
                            title=f"Approval Expiring: {req.action.action}",
                            reason=reason_msg,
                            action_hint="Review and decide in Approval Center.",
                            reference_id=req.approval_id,
                            created_at=now,
                            acknowledged=aid in self._acknowledged_ids,
                        )
                    )

        # 2. Workflows status
        for wf in self._workflows.list(limit=20):
            wfid = wf.definition.workflow_id
            if wf.status is WorkflowStatus.FAILED:
                err = wf.error_code or "unspecified error"
                aid = f"att-wf-fail-{wfid}"
                items.append(
                    AttentionItem(
                        attention_id=aid,
                        category=AttentionCategory.WORKFLOW_FAILED,
                        priority=AttentionPriority.HIGH,
                        title=f"Workflow Failed: {wf.definition.goal[:40]}",
                        reason=f"Workflow halted with error: {err}",
                        action_hint="Inspect task attempts and consider retry.",
                        reference_id=wfid,
                        created_at=now,
                        acknowledged=aid in self._acknowledged_ids,
                    )
                )
            elif wf.status is WorkflowStatus.WAITING_FOR_APPROVAL:
                reason = wf.waiting_reason or "Waiting for human approval"
                aid = f"att-wf-wait-{wfid}"
                items.append(
                    AttentionItem(
                        attention_id=aid,
                        category=AttentionCategory.WORKFLOW_BLOCKED,
                        priority=AttentionPriority.MEDIUM,
                        title=f"Workflow Paused: {wf.definition.goal[:40]}",
                        reason=reason,
                        action_hint="Review pending approval to resume execution.",
                        reference_id=wfid,
                        created_at=now,
                        acknowledged=aid in self._acknowledged_ids,
                    )
                )

        return tuple(items)

    def acknowledge(self, attention_id: str) -> bool:
        self._acknowledged_ids.add(attention_id)
        return True


__all__ = [
    "AttentionCategory",
    "AttentionItem",
    "AttentionPriority",
    "AttentionService",
]
