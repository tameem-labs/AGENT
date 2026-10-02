from __future__ import annotations

from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from zyro.api.app import create_app
from zyro.models.gemini import GeminiTransport

PASSWORD = "correct horse battery staple"
KEY = "test-gemini-key-not-a-provider-credential"


class FakeGeminiTransport(GeminiTransport):
    def __init__(self, *, reject: bool = False, reject_post_code: int | None = None) -> None:
        self.reject = reject
        self.reject_post_code = reject_post_code
        self.calls: list[tuple[str, str, dict[str, str], dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]:
        self.calls.append((method, url, headers, payload))
        assert timeout > 0
        if self.reject:
            raise HTTPError(url, 403, "forbidden", Message(), None)
        if method == "POST" and self.reject_post_code is not None:
            raise HTTPError(url, self.reject_post_code, "provider failure", Message(), None)
        if method == "GET":
            return {"name": "models/gemini-2.5-flash"}
        return {
            "candidates": [
                {"content": {"parts": [{"text": "Gemini completed the executive response."}]}}
            ],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7},
        }


def login(client: TestClient) -> str:
    assert client.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 201
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    return str(response.json()["csrf_token"])


def test_first_run_configures_encrypted_gemini_and_routes_real_chat(tmp_path: Path) -> None:
    transport = FakeGeminiTransport()
    data_dir = tmp_path / "product"
    with TestClient(create_app(data_dir, gemini_transport=transport)) as client:
        csrf = login(client)
        headers = {"X-ZYRO-CSRF": csrf}
        before = client.get("/api/setup").json()
        assert before["completed"] is False
        assert before["gemini"]["status"] == "NOT_CONFIGURED"

        configured = client.put(
            "/api/models/gemini",
            json={"api_key": KEY, "model_id": "gemini-2.5-flash"},
            headers=headers,
        )
        assert configured.status_code == 200
        assert configured.json()["status"] == "CONFIGURED"
        assert KEY not in configured.text
        assert KEY.encode() not in (data_dir / "integrations.sqlite").read_bytes()

        assert (
            client.post(
                "/api/setup/complete",
                json={"use_local_fallback": False},
                headers=headers,
            ).status_code
            == 200
        )
        result = client.post(
            "/api/chat", json={"message": "Plan today's priorities"}, headers=headers
        ).json()
        assert result["message"] == "Gemini completed the executive response."
        assert result["task"]["result"]["provider_id"] == "google.gemini"
        assert [call[0] for call in transport.calls] == ["GET", "POST"]
        assert all(KEY not in call[1] for call in transport.calls)

        removed = client.delete("/api/models/gemini", headers=headers)
        assert removed.status_code == 204
        assert client.get("/api/models").json()["providers"][0]["status"] == "NOT_CONFIGURED"


def test_invalid_gemini_key_is_not_persisted_or_returned(tmp_path: Path) -> None:
    transport = FakeGeminiTransport(reject=True)
    data_dir = tmp_path / "product"
    with TestClient(create_app(data_dir, gemini_transport=transport)) as client:
        csrf = login(client)
        response = client.put(
            "/api/models/gemini",
            json={"api_key": KEY, "model_id": "gemini-2.5-flash"},
            headers={"X-ZYRO-CSRF": csrf},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["status"] == "INVALID"
        assert KEY not in response.text
        assert KEY.encode() not in (data_dir / "integrations.sqlite").read_bytes()
        assert client.get("/api/models").json()["providers"][0]["configured"] is False


def test_retryable_gemini_failure_uses_one_honest_local_fallback(tmp_path: Path) -> None:
    transport = FakeGeminiTransport(reject_post_code=429)
    with TestClient(create_app(tmp_path / "product", gemini_transport=transport)) as client:
        csrf = login(client)
        headers = {"X-ZYRO-CSRF": csrf}
        assert (
            client.put(
                "/api/models/gemini",
                json={"api_key": KEY, "model_id": "gemini-2.5-flash"},
                headers=headers,
            ).status_code
            == 200
        )
        result = client.post(
            "/api/chat", json={"message": "Plan today's priorities"}, headers=headers
        ).json()
        assert result["task"]["status"] == "DONE"
        assert result["task"]["result"]["provider_id"] == "zyro.local"
        assert "local-development adapter" in result["message"]
        assert [call[0] for call in transport.calls] == ["GET", "POST"]
