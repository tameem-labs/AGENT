"""Bounded read-only official provider API operations for connected accounts."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from zyro.integrations.oauth import IntegrationService


class ConnectedAccountActions:
    """Authenticated application boundary; write effects remain behind future Tool approval."""

    def __init__(self, integrations: IntegrationService) -> None:
        self._integrations = integrations

    def execute(self, connection_id: str, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        connection = next(
            (
                item
                for item in self._integrations.connections()
                if item.connection_id == connection_id
            ),
            None,
        )
        if connection is None:
            raise KeyError("integration connection does not exist")
        self._require_scope(connection.integration_id, action, connection.scopes)
        token = self._integrations.credential_for_tool(connection_id)
        if connection.integration_id == "google":
            return self._google(action, arguments, token.access_token)
        if connection.integration_id == "github":
            return self._github(action, arguments, token.access_token)
        if connection.integration_id == "instagram":
            return self._instagram(action, arguments, token.access_token)
        raise ValueError("this connection exposes no official provider actions")

    @staticmethod
    def _require_scope(integration: str, action: str, scopes: tuple[str, ...]) -> None:
        if integration != "google":
            if integration == "github" and not ({"repo", "public_repo"} & set(scopes)):
                raise ValueError("GitHub connection lacks a repository read scope")
            if integration == "instagram" and "user_profile" not in scopes:
                raise ValueError("Instagram connection lacks the user_profile scope")
            return
        accepted: set[str]
        if action.startswith("gmail."):
            accepted = {
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://mail.google.com/",
            }
        elif action.startswith("drive."):
            accepted = {
                "https://www.googleapis.com/auth/drive.metadata.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/drive.file",
            }
        elif action.startswith("calendar."):
            accepted = {
                "https://www.googleapis.com/auth/calendar.readonly",
                "https://www.googleapis.com/auth/calendar.events",
            }
        else:
            raise ValueError("unsupported Google action")
        if not accepted.intersection(scopes):
            raise ValueError("Google connection lacks the required OAuth scope")

    def _google(self, action: str, args: dict[str, Any], token: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if action in {"gmail.list", "gmail.search"}:
            query: dict[str, Any] = {"maxResults": min(100, int(args.get("limit", 20)))}
            if action == "gmail.search":
                query["q"] = str(args.get("query", ""))[:500]
            return self._get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urlencode(query),
                headers,
            )
        if action == "gmail.read_message":
            identity = quote(str(args["message_id"]), safe="")
            return self._get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{identity}?format=full",
                headers,
            )
        if action == "gmail.read_thread":
            identity = quote(str(args["thread_id"]), safe="")
            return self._get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{identity}?format=full",
                headers,
            )
        if action in {"drive.list", "drive.search"}:
            drive_query: dict[str, Any] = {
                "pageSize": min(100, int(args.get("limit", 20))),
                "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink),nextPageToken",
            }
            if action == "drive.search":
                text = str(args.get("query", "")).replace("'", "\\'")[:300]
                drive_query["q"] = f"name contains '{text}' and trashed = false"
            return self._get(
                "https://www.googleapis.com/drive/v3/files?" + urlencode(drive_query), headers
            )
        if action == "drive.metadata":
            identity = quote(str(args["file_id"]), safe="")
            return self._get(
                f"https://www.googleapis.com/drive/v3/files/{identity}?"
                + urlencode({"fields": "id,name,mimeType,modifiedTime,size,webViewLink"}),
                headers,
            )
        if action == "calendar.list_calendars":
            return self._get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList", headers
            )
        if action in {"calendar.list_events", "calendar.search_events"}:
            calendar = quote(str(args.get("calendar_id", "primary")), safe="")
            calendar_query: dict[str, Any] = {
                "maxResults": min(100, int(args.get("limit", 20))),
                "singleEvents": "true",
            }
            if action == "calendar.search_events":
                calendar_query["q"] = str(args.get("query", ""))[:300]
            return self._get(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar}/events?"
                + urlencode(calendar_query),
                headers,
            )
        if action == "calendar.read_event":
            calendar = quote(str(args.get("calendar_id", "primary")), safe="")
            event = quote(str(args["event_id"]), safe="")
            return self._get(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar}/events/{event}",
                headers,
            )
        raise ValueError("unsupported or consequential Google action")

    def _github(self, action: str, args: dict[str, Any], token: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ZYRO/0.14",
        }
        if action == "github.repositories":
            return {
                "items": self._get_list("https://api.github.com/user/repos?per_page=100", headers)
            }
        owner = quote(str(args.get("owner", "")), safe="")
        repo = quote(str(args.get("repo", "")), safe="")
        if not owner or not repo:
            raise ValueError("GitHub owner and repository are required")
        base = f"https://api.github.com/repos/{owner}/{repo}"
        paths = {
            "github.branches": "/branches?per_page=100",
            "github.commits": "/commits?per_page=100",
            "github.issues": "/issues?per_page=100",
            "github.pull_requests": "/pulls?per_page=100",
        }
        if action in paths:
            return {"items": self._get_list(base + paths[action], headers)}
        if action == "github.file":
            path = quote(str(args.get("path", "")), safe="/")
            return self._get(base + f"/contents/{path}", headers)
        if action == "github.status":
            sha = quote(str(args.get("ref", "HEAD")), safe="")
            return self._get(base + f"/commits/{sha}/status", headers)
        raise ValueError("unsupported or consequential GitHub action")

    def _instagram(self, action: str, args: dict[str, Any], token: str) -> dict[str, Any]:
        if action not in {"instagram.profile", "instagram.media"}:
            raise ValueError("unsupported Instagram action")
        fields = (
            "id,username"
            if action == "instagram.profile"
            else "id,caption,media_type,permalink,timestamp"
        )
        path = "me" if action == "instagram.profile" else "me/media"
        return self._get(
            f"https://graph.instagram.com/{path}?" + urlencode({"fields": fields}),
            {"Accept": "application/json", "Authorization": f"Bearer {token}"},
        )

    @staticmethod
    def _get(url: str, headers: dict[str, str]) -> dict[str, Any]:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            value = json.loads(response.read(2_000_001))
        if not isinstance(value, dict):
            raise ValueError("provider returned malformed response")
        return value

    @staticmethod
    def _get_list(url: str, headers: dict[str, str]) -> list[Any]:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            value = json.loads(response.read(2_000_001))
        if not isinstance(value, list):
            raise ValueError("provider returned malformed response")
        return value


__all__ = ["ConnectedAccountActions"]
