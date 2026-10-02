from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from zyro.api.app import create_app


def test_google_oauth_configuration_is_encrypted_dynamic_and_uses_official_endpoint(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "product"
    with TestClient(create_app(data_dir)) as client:
        password = "official oauth owner password"
        client.post("/api/auth/setup", json={"password": password})
        login = client.post("/api/auth/login", json={"password": password}).json()
        headers = {"X-ZYRO-CSRF": login["csrf_token"]}
        secret = "google-oauth-client-secret-test-only"
        configured = client.put(
            "/api/integrations/providers/google/configuration",
            json={
                "client_id": "google-client-id.apps.googleusercontent.com",
                "client_secret": secret,
            },
            headers=headers,
        )
        assert configured.status_code == 200
        definitions = client.get("/api/integrations").json()["definitions"]
        google = next(item for item in definitions if item["integration_id"] == "google")
        assert google["oauth_configured"] is True
        assert secret not in configured.text
        assert secret.encode() not in (data_dir / "integrations.sqlite").read_bytes()

        started = client.post(
            "/api/integrations/google/oauth/start",
            json={"scopes": ["openid", "email"]},
            headers=headers,
        )
        assert started.status_code == 200
        url = started.json()["authorization_url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "code_challenge_method=S256" in url
        assert secret not in url

        removed = client.delete("/api/integrations/providers/google/configuration", headers=headers)
        assert removed.status_code == 204
        definitions = client.get("/api/integrations").json()["definitions"]
        google = next(item for item in definitions if item["integration_id"] == "google")
        assert google["oauth_configured"] is False
