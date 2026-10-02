from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from zyro.core.errors import ErrorInfo
from zyro.core.risk import RiskClass
from zyro.security.approval import ApprovalService
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
from zyro.tools.contracts import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolHandlerResult,
    ToolResultStatus,
)
from zyro.tools.executor import ToolExecutor
from zyro.tools.registry import ToolRegistry

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@dataclass
class WriteHandler:
    calls: list[tuple[ToolExecutionContext, dict[str, Any]]] = field(default_factory=list)

    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        self.calls.append((context, dict(arguments)))
        return ToolHandlerResult.success({"echo": dict(arguments)})


@dataclass
class UnknownHandler:
    calls: list[tuple[ToolExecutionContext, dict[str, Any]]] = field(default_factory=list)

    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        self.calls.append((context, dict(arguments)))
        return ToolHandlerResult.unknown_outcome(
            ErrorInfo(
                code="external_outcome_unknown",
                message="Dispatch was acknowledged but completion is unknown.",
                error_type="UnknownExternalOutcome",
            ),
            {"dispatch_id": "dispatch-1"},
        )


def definition(risk: RiskClass) -> ToolDefinition:
    return ToolDefinition(
        tool_id="writer",
        name="Writer",
        version="1.0.0",
        description="Writes one bounded record.",
        capabilities=frozenset({"write"}),
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler_id="writer-handler",
        output_schema={
            "type": "object",
            "properties": {"echo": {"type": "object"}},
            "required": ["echo"],
        },
        risk_class=risk,
    )


def tool_call(**changes: object) -> ToolCall:
    values: dict[str, object] = {
        "tool_id": "writer",
        "arguments": {"value": 1},
        "request_id": "request-1",
        "task_id": "task-1",
        "agent_id": "agent-1",
        "instance_id": "instance-1",
        "correlation_id": "correlation-1",
        "requester_id": "owner-1",
        "capability": "write",
        "target": "record-1",
        "purpose": "Update the requested record.",
        "expected_effect": "The record value changes.",
    }
    values.update(changes)
    return ToolCall(**values)  # type: ignore[arg-type]


def system(
    risk: RiskClass,
    *,
    handler: WriteHandler | UnknownHandler | None = None,
) -> tuple[ToolExecutor, ApprovalService, PermissionStore, WriteHandler | UnknownHandler]:
    store = PermissionStore()
    store.add(
        Permission(
            permission_id="permission-1",
            principal_id="agent-1",
            capability="write",
            scope=PermissionScope(tool_id="writer", action="execute"),
            policy_version="policy-v1",
            issued_at=NOW - timedelta(minutes=1),
        )
    )
    permissions = PermissionEvaluator(
        store,
        PrincipalDirectory(("agent-1", "owner-1", "human-1")),
        frozenset({"write"}),
        policy_version="policy-v1",
        clock=lambda: NOW,
        id_factory=lambda: "permission-decision-1",
    )
    approvals = ApprovalService(
        frozenset({"human-1"}),
        clock=lambda: NOW,
        id_factory=lambda: "approval-1",
    )
    authorizer = ToolAuthorizationService(
        permissions,
        RiskPolicy(policy_version="policy-v1"),
        approvals,
        clock=lambda: NOW,
    )
    actual_handler = handler or WriteHandler()
    registry = ToolRegistry()
    registry.register(definition(risk), actual_handler)
    return ToolExecutor(registry, authorizer), approvals, store, actual_handler


def test_automatic_action_still_requires_scoped_permission() -> None:
    executor, _, store, handler = system(RiskClass.AUTOMATIC)
    store.set_status("permission-1", PermissionStatus.REVOKED)

    result = executor.execute(tool_call())

    assert result.status is ToolResultStatus.PERMISSION_DENIED
    assert result.permission_decision_id == "permission-decision-1"
    assert handler.calls == []


def test_strict_action_creates_pending_approval_without_calling_handler() -> None:
    executor, approvals, _, handler = system(RiskClass.STRICT_AUTHORIZATION)

    result = executor.execute(tool_call())

    assert result.status is ToolResultStatus.APPROVAL_PENDING
    assert result.approval_id == "approval-1"
    assert approvals.get("approval-1").requester_id == "owner-1"
    assert handler.calls == []


def test_approved_exact_action_executes_and_propagates_audit_ids() -> None:
    executor, approvals, _, handler = system(RiskClass.STRICT_AUTHORIZATION)
    pending = executor.execute(tool_call())
    approvals.approve("approval-1", "human-1", "Reviewed exact target and effect.")

    result = executor.execute(tool_call(approval_id=pending.approval_id))

    assert result.status is ToolResultStatus.SUCCESS
    assert result.permission_id == "permission-1"
    assert result.permission_decision_id == "permission-decision-1"
    assert result.approval_id == "approval-1"
    assert len(handler.calls) == 1


@pytest.mark.parametrize(
    ("decision", "status"),
    [
        ("deny", ToolResultStatus.APPROVAL_DENIED),
        ("cancel", ToolResultStatus.APPROVAL_DENIED),
    ],
)
def test_denied_or_cancelled_action_never_calls_handler(
    decision: str,
    status: ToolResultStatus,
) -> None:
    executor, approvals, _, handler = system(RiskClass.STRICT_AUTHORIZATION)
    pending = executor.execute(tool_call())
    if decision == "deny":
        approvals.deny("approval-1", "human-1", "Denied.")
    else:
        approvals.cancel("approval-1", "owner-1", "Cancelled.")

    result = executor.execute(tool_call(approval_id=pending.approval_id))

    assert result.status is status
    assert handler.calls == []


def test_material_change_after_approval_is_blocked() -> None:
    executor, approvals, _, handler = system(RiskClass.STRICT_AUTHORIZATION)
    executor.execute(tool_call())
    approvals.approve("approval-1", "human-1", "Reviewed exact action.")

    result = executor.execute(tool_call(arguments={"value": 2}, approval_id="approval-1"))

    assert result.status is ToolResultStatus.APPROVAL_INVALID
    assert result.error is not None
    assert result.error.code == "approval_action_mismatch"
    assert handler.calls == []


def test_revocation_after_approval_still_blocks_execution() -> None:
    executor, approvals, store, handler = system(RiskClass.STRICT_AUTHORIZATION)
    executor.execute(tool_call())
    approvals.approve("approval-1", "human-1", "Reviewed exact action.")
    store.set_status("permission-1", PermissionStatus.REVOKED)

    result = executor.execute(tool_call(approval_id="approval-1"))

    assert result.status is ToolResultStatus.PERMISSION_DENIED
    assert handler.calls == []


def test_unknown_handler_outcome_is_not_fabricated_as_success_or_failure() -> None:
    unknown_handler = UnknownHandler()
    executor, _, _, _ = system(RiskClass.AUTOMATIC, handler=unknown_handler)

    result = executor.execute(tool_call())

    assert result.status is ToolResultStatus.UNKNOWN
    assert not result.succeeded
    assert result.outcome_unknown
    assert result.output == {"dispatch_id": "dispatch-1"}
