from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from zyro.api.app import create_app

PASSWORD = "correct horse battery staple"


def authenticate_existing(client: TestClient) -> str:
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def authenticate(client: TestClient) -> str:
    assert client.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 201
    return authenticate_existing(client)


def test_authenticated_ui_api_runs_real_executive_workflow_and_resource_path(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "product")
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert "ZYRO" in client.get("/").text
        csrf = authenticate(client)

        denied = client.post("/api/chat", json={"message": "Plan my day"})
        assert denied.status_code == 401

        response = client.post(
            "/api/chat",
            json={"message": "Find qualified leads and prepare outreach"},
            headers={"X-ZYRO-CSRF": csrf},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["task"]["status"] == "DONE"
        assert result["task"]["verification"]["outcome"] == "VERIFIED"
        assert result["workflow"]["status"] == "COMPLETED"
        assert "not configured" in result["message"].lower()

        tasks = client.get("/api/tasks").json()
        workflows = client.get("/api/workflows").json()
        assert len(tasks) == len(workflows) == 1
        assert tasks[0]["workflow_id"] == workflows[0]["workflow_id"]

        connection = sqlite3.connect(tmp_path / "product" / "resources.sqlite")
        assert connection.execute("SELECT SUM(units) FROM usage_events").fetchone()[0] > 0
        connection.close()


def test_local_authentication_csrf_logout_and_development_oauth(tmp_path: Path) -> None:
    data_dir = tmp_path / "product"
    app = create_app(data_dir)
    with TestClient(app) as client:
        csrf = authenticate(client)
        assert client.get("/api/auth/me").status_code == 200
        assert (
            client.post(
                "/api/chat",
                json={"message": "hello"},
                headers={"X-ZYRO-CSRF": "forged"},
            ).status_code
            == 401
        )

        started = client.post(
            "/api/integrations/development/oauth/start",
            json={"scopes": ["profile"]},
            headers={"X-ZYRO-CSRF": csrf},
        )
        assert started.status_code == 200
        authorization_url = started.json()["authorization_url"]
        provider_redirect = client.get(authorization_url, follow_redirects=False)
        assert provider_redirect.status_code in {302, 307}
        callback_url = provider_redirect.headers["location"]
        client.cookies.clear()  # Real Strict cookies are absent after a cross-site redirect.
        callback = client.get(callback_url, follow_redirects=False)
        assert callback.status_code in {302, 307}
        # Re-authenticate to inspect the completed server-side connection.
        csrf = authenticate_existing(client)
        integrations = client.get("/api/integrations").json()
        assert integrations["connections"][0]["status"] == "CONNECTED"

        database_bytes = (data_dir / "integrations.sqlite").read_bytes()
        assert b"development-access-" not in database_bytes
        assert b"development-refresh-" not in database_bytes

        connection_id = integrations["connections"][0]["connection_id"]
        disconnected = client.post(
            f"/api/integrations/connections/{connection_id}/disconnect",
            headers={"X-ZYRO-CSRF": csrf},
        )
        assert disconnected.json()["status"] == "DISCONNECTED"

        assert client.post("/api/auth/logout", headers={"X-ZYRO-CSRF": csrf}).status_code == 204
        assert client.get("/api/auth/me").status_code == 401
