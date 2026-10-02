"""Unit tests for consequential write tools and strict authorization enforcement."""

from __future__ import annotations

from unittest.mock import MagicMock

from zyro.core.risk import RiskClass
from zyro.integrations.actions import ConnectedAccountActions
from zyro.integrations.writes import (
    CalendarCreateEventToolHandler,
    CRMCreateLeadToolHandler,
    GitHubCreatePRToolHandler,
    GmailSendToolHandler,
    register_consequential_write_tools,
)
from zyro.tools.contracts import ToolExecutionContext
from zyro.tools.registry import ToolRegistry


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("tool.write", "req-1", "task-1", "agent-1", "inst-1", "corr-1")


def test_gmail_send_handler() -> None:
    mock_actions = MagicMock(spec=ConnectedAccountActions)
    mock_actions.execute.return_value = {"id": "msg-12345", "threadId": "th-999"}

    handler = GmailSendToolHandler(mock_actions)

    # Missing arguments
    err = handler.execute(_ctx(), {"connection_id": "c1", "to": ""})
    assert not err.succeeded
    assert err.error is not None
    assert err.error.code == "invalid_arguments"

    # Valid send
    res = handler.execute(
        _ctx(),
        {"connection_id": "c1", "to": "client@example.com", "subject": "Proposal", "body": "Hello"},
    )
    assert res.succeeded
    assert res.output is not None
    assert res.output["message_id"] == "msg-12345"
    assert res.output["status"] == "SENT"
    mock_actions.execute.assert_called_once_with(
        "c1",
        "gmail.send_message",
        {"to": "client@example.com", "subject": "Proposal", "body": "Hello"},
    )


def test_calendar_create_event_handler() -> None:
    mock_actions = MagicMock(spec=ConnectedAccountActions)
    mock_actions.execute.return_value = {"id": "evt-777", "htmlLink": "https://calendar.google.com/event/777"}

    handler = CalendarCreateEventToolHandler(mock_actions)

    # Missing arguments
    err = handler.execute(_ctx(), {"connection_id": "c1", "summary": ""})
    assert not err.succeeded
    assert err.error is not None
    assert err.error.code == "invalid_arguments"

    # Valid event creation
    res = handler.execute(
        _ctx(),
        {
            "connection_id": "c1",
            "summary": "Project Kickoff",
            "start_time": "2026-10-03T10:00:00Z",
            "end_time": "2026-10-03T11:00:00Z",
        },
    )
    assert res.succeeded
    assert res.output is not None
    assert res.output["event_id"] == "evt-777"
    assert res.output["status"] == "CREATED"


def test_github_create_pr_handler() -> None:
    mock_actions = MagicMock(spec=ConnectedAccountActions)
    mock_actions.execute.return_value = {"number": 42, "html_url": "https://github.com/org/repo/pull/42"}

    handler = GitHubCreatePRToolHandler(mock_actions)

    # Missing arguments
    err = handler.execute(_ctx(), {"connection_id": "c1", "owner": "org"})
    assert not err.succeeded
    assert err.error is not None
    assert err.error.code == "invalid_arguments"

    # Valid PR
    res = handler.execute(
        _ctx(),
        {
            "connection_id": "c1",
            "owner": "org",
            "repo": "repo",
            "title": "feat: architecture verification",
            "head": "feature-branch",
        },
    )
    assert res.succeeded
    assert res.output is not None
    assert res.output["pr_number"] == 42
    assert res.output["status"] == "CREATED"


def test_crm_create_lead_handler() -> None:
    handler = CRMCreateLeadToolHandler()

    # Missing arguments
    err = handler.execute(_ctx(), {"name": "", "email": ""})
    assert not err.succeeded
    assert err.error is not None
    assert err.error.code == "invalid_arguments"

    # Valid lead
    res = handler.execute(
        _ctx(),
        {"name": "Alice Corp", "email": "alice@corp.com", "company": "Alice Co"},
    )
    assert res.succeeded
    assert res.output is not None
    assert res.output["name"] == "Alice Corp"
    assert res.output["email"] == "alice@corp.com"
    assert res.output["status"] == "CREATED_IN_CRM"


def test_register_consequential_write_tools_risk_classes() -> None:
    reg = ToolRegistry()
    mock_actions = MagicMock(spec=ConnectedAccountActions)
    register_consequential_write_tools(reg, mock_actions)

    gmail = reg.get("google.gmail_send")
    cal = reg.get("google.calendar_create")
    gh = reg.get("github.create_pr")
    crm = reg.get("crm.create_lead")

    assert gmail.definition.risk_class is RiskClass.STRICT_AUTHORIZATION
    assert cal.definition.risk_class is RiskClass.STRICT_AUTHORIZATION
    assert gh.definition.risk_class is RiskClass.STRICT_AUTHORIZATION
    assert crm.definition.risk_class is RiskClass.STRICT_AUTHORIZATION
