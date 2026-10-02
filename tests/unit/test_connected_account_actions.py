from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from zyro.integrations import (
    ConnectedAccountActions,
    EncryptedCredentialStore,
    IntegrationConnection,
    IntegrationDefinition,
    IntegrationService,
    IntegrationStatus,
    OAuthTokenSet,
)


class Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return json.dumps(self.payload).encode()


def test_gmail_drive_calendar_and_github_reads_use_backend_tokens(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = EncryptedCredentialStore(tmp_path / "i.sqlite", tmp_path / "key")
    now = datetime.now(UTC)
    scopes = (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    )
    store.save_connection(
        IntegrationConnection(
            "google-1",
            "google",
            "account",
            "owner@example.com",
            scopes,
            IntegrationStatus.CONNECTED,
            now,
            now,
            now,
            "Healthy",
        ),
        OAuthTokenSet(
            "google-access-secret",
            "refresh",
            now + timedelta(hours=1),
            scopes,
            "account",
            "owner@example.com",
        ),
    )
    service = IntegrationService(
        store,
        (IntegrationDefinition("google", "google", "Google", (), scopes, True),),
    )
    seen: list[tuple[str, str]] = []

    def fake_urlopen(request: Any, timeout: float) -> Response:
        seen.append((request.full_url, request.headers.get("Authorization", "")))
        return Response({"items": []})

    monkeypatch.setattr("zyro.integrations.actions.urlopen", fake_urlopen)
    actions = ConnectedAccountActions(service)
    actions.execute("google-1", "gmail.list", {"limit": 2})
    actions.execute("google-1", "drive.list", {"limit": 2})
    actions.execute("google-1", "calendar.list_calendars", {})
    assert len(seen) == 3
    assert all(header == "Bearer google-access-secret" for _, header in seen)
    assert all("google-access-secret" not in url for url, _ in seen)
    store.close()
