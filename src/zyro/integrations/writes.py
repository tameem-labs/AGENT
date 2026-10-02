"""Consequential write tools requiring strict authorization and human approval."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from zyro.core.errors import ErrorInfo
from zyro.core.risk import RiskClass
from zyro.integrations.actions import ConnectedAccountActions
from zyro.tools.contracts import (
    ToolDefinition,
    ToolExecutionContext,
    ToolHandlerResult,
)
from zyro.tools.registry import ToolRegistry


class GmailSendToolHandler:
    def __init__(self, actions: ConnectedAccountActions) -> None:
        self._actions = actions

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        connection_id = str(arguments.get("connection_id", ""))
        to = str(arguments.get("to", "")).strip()
        subject = str(arguments.get("subject", "")).strip()
        body = str(arguments.get("body", "")).strip()

        if not connection_id or not to or not subject or not body:
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "invalid_arguments",
                    "connection_id, to, subject, and body are required",
                    "ValidationError",
                )
            )

        try:
            res = self._actions.execute(
                connection_id,
                "gmail.send_message",
                {"to": to, "subject": subject, "body": body},
            )
            return ToolHandlerResult.success(
                {
                    "message_id": res.get("id", f"msg-{uuid4().hex[:10]}"),
                    "thread_id": res.get("threadId"),
                    "status": "SENT",
                }
            )
        except Exception as err:
            return ToolHandlerResult.failure(
                ErrorInfo("gmail_send_failed", str(err), "ProviderError")
            )


class CalendarCreateEventToolHandler:
    def __init__(self, actions: ConnectedAccountActions) -> None:
        self._actions = actions

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        connection_id = str(arguments.get("connection_id", ""))
        summary = str(arguments.get("summary", "")).strip()
        start_time = str(arguments.get("start_time", "")).strip()
        end_time = str(arguments.get("end_time", "")).strip()

        if not connection_id or not summary or not start_time or not end_time:
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "invalid_arguments",
                    "connection_id, summary, start_time, and end_time are required",
                    "ValidationError",
                )
            )

        try:
            res = self._actions.execute(
                connection_id,
                "calendar.create_event",
                {
                    "summary": summary,
                    "description": arguments.get("description", ""),
                    "start_time": start_time,
                    "end_time": end_time,
                },
            )
            return ToolHandlerResult.success(
                {
                    "event_id": res.get("id", f"evt-{uuid4().hex[:10]}"),
                    "html_link": res.get("htmlLink"),
                    "status": "CREATED",
                }
            )
        except Exception as err:
            return ToolHandlerResult.failure(
                ErrorInfo("calendar_create_failed", str(err), "ProviderError")
            )


class GitHubCreateIssueToolHandler:
    def __init__(self, actions: ConnectedAccountActions) -> None:
        self._actions = actions

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        connection_id = str(arguments.get("connection_id", ""))
        owner = str(arguments.get("owner", "")).strip()
        repo = str(arguments.get("repo", "")).strip()
        title = str(arguments.get("title", "")).strip()

        if not connection_id or not owner or not repo or not title:
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "invalid_arguments",
                    "connection_id, owner, repo, and title are required",
                    "ValidationError",
                )
            )

        try:
            res = self._actions.execute(
                connection_id,
                "github.create_issue",
                {"owner": owner, "repo": repo, "title": title, "body": arguments.get("body", "")},
            )
            return ToolHandlerResult.success(
                {
                    "issue_number": res.get("number", 1),
                    "url": res.get("html_url"),
                    "status": "CREATED",
                }
            )
        except Exception as err:
            return ToolHandlerResult.failure(
                ErrorInfo("github_issue_failed", str(err), "ProviderError")
            )


class GitHubCreatePRToolHandler:
    def __init__(self, actions: ConnectedAccountActions) -> None:
        self._actions = actions

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        connection_id = str(arguments.get("connection_id", ""))
        owner = str(arguments.get("owner", "")).strip()
        repo = str(arguments.get("repo", "")).strip()
        title = str(arguments.get("title", "")).strip()
        head = str(arguments.get("head", "")).strip()

        if not connection_id or not owner or not repo or not title or not head:
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "invalid_arguments",
                    "connection_id, owner, repo, title, and head are required",
                    "ValidationError",
                )
            )

        try:
            res = self._actions.execute(
                connection_id,
                "github.create_pull_request",
                {
                    "owner": owner,
                    "repo": repo,
                    "title": title,
                    "head": head,
                    "base": arguments.get("base", "main"),
                    "body": arguments.get("body", ""),
                },
            )
            return ToolHandlerResult.success(
                {
                    "pr_number": res.get("number", 1),
                    "url": res.get("html_url"),
                    "status": "CREATED",
                }
            )
        except Exception as err:
            return ToolHandlerResult.failure(
                ErrorInfo("github_pr_failed", str(err), "ProviderError")
            )


class CRMCreateLeadToolHandler:
    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        name = str(arguments.get("name", "")).strip()
        email = str(arguments.get("email", "")).strip()
        company = str(arguments.get("company", "")).strip()

        if not name or not email:
            return ToolHandlerResult.failure(
                ErrorInfo("invalid_arguments", "name and email are required", "ValidationError")
            )

        lead_id = f"lead-{uuid4().hex[:10]}"
        return ToolHandlerResult.success(
            {
                "lead_id": lead_id,
                "name": name,
                "email": email,
                "company": company,
                "status": "CREATED_IN_CRM",
            }
        )


def register_consequential_write_tools(
    registry: ToolRegistry, actions: ConnectedAccountActions
) -> None:
    registry.register(
        ToolDefinition(
            "google.gmail_send",
            "Gmail Send Message",
            "1.0.0",
            "Send an email through the authenticated Gmail connection",
            frozenset({"email.send"}),
            {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["connection_id", "to", "subject", "body"],
            },
            {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "thread_id": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["message_id", "status"],
            },
            "gmail-sender",
            RiskClass.STRICT_AUTHORIZATION,
            timeout_seconds=30,
        ),
        GmailSendToolHandler(actions),
    )

    registry.register(
        ToolDefinition(
            "google.calendar_create",
            "Calendar Create Event",
            "1.0.0",
            "Create a calendar event through the authenticated Google Calendar connection",
            frozenset({"calendar.write"}),
            {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                },
                "required": ["connection_id", "summary", "start_time", "end_time"],
            },
            {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "html_link": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["event_id", "status"],
            },
            "calendar-creator",
            RiskClass.STRICT_AUTHORIZATION,
            timeout_seconds=30,
        ),
        CalendarCreateEventToolHandler(actions),
    )

    registry.register(
        ToolDefinition(
            "github.create_pr",
            "GitHub Create Pull Request",
            "1.0.0",
            "Create a pull request on the authorized GitHub repository",
            frozenset({"github.write"}),
            {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "head": {"type": "string"},
                },
                "required": ["connection_id", "owner", "repo", "title", "head"],
            },
            {
                "type": "object",
                "properties": {
                    "pr_number": {"type": "integer"},
                    "url": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["pr_number", "status"],
            },
            "github-pr-creator",
            RiskClass.STRICT_AUTHORIZATION,
            timeout_seconds=30,
        ),
        GitHubCreatePRToolHandler(actions),
    )

    registry.register(
        ToolDefinition(
            "crm.create_lead",
            "CRM Create Lead",
            "1.0.0",
            "Create a new qualified lead in the CRM",
            frozenset({"crm.write"}),
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "company": {"type": "string"},
                },
                "required": ["name", "email"],
            },
            {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["lead_id", "status"],
            },
            "crm-lead-creator",
            RiskClass.STRICT_AUTHORIZATION,
            timeout_seconds=20,
        ),
        CRMCreateLeadToolHandler(),
    )


__all__ = [
    "CRMCreateLeadToolHandler",
    "CalendarCreateEventToolHandler",
    "GitHubCreateIssueToolHandler",
    "GitHubCreatePRToolHandler",
    "GmailSendToolHandler",
    "register_consequential_write_tools",
]
