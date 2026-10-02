from __future__ import annotations

import json
from email.message import Message
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.integration.test_gemini_product import FakeGeminiTransport
from zyro.api.app import create_app


class Headers(Message):
    def get_content_type(self) -> str:
        return "text/html"


class Response:
    def __init__(self, body: bytes, url: str, *, html: bool = False) -> None:
        self._body = body
        self._url = url
        self.headers = Headers()
        if not html:
            self.headers.set_type("application/json")

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int | None = None) -> bytes:
        return self._body if limit is None else self._body[:limit]

    def geturl(self) -> str:
        return self._url


def test_research_request_runs_canonical_agent_tools_resources_and_sources(
    tmp_path: Path, monkeypatch: Any
) -> None:
    sources = ["https://example.com/a", "https://example.org/b"]

    def fake_urlopen(request: Any, timeout: float) -> Response:
        url = str(request.full_url)
        assert timeout > 0
        if "api.search.brave.com" in url:
            payload = {
                "web": {
                    "results": [
                        {"title": "A", "url": sources[0], "description": "First source"},
                        {"title": "B", "url": sources[1], "description": "Second source"},
                    ]
                }
            }
            return Response(json.dumps(payload).encode(), url)
        return Response(
            f"<html><body>Independent evidence from {url}</body></html>".encode(),
            url,
            html=True,
        )

    monkeypatch.setattr("zyro.research.runtime.urlopen", fake_urlopen)
    monkeypatch.setattr("zyro.research.runtime._open_public", fake_urlopen)
    monkeypatch.setattr(
        "zyro.research.runtime.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    gemini = FakeGeminiTransport()
    with TestClient(create_app(tmp_path / "product", gemini_transport=gemini)) as client:
        password = "research vertical owner password"
        assert client.post("/api/auth/setup", json={"password": password}).status_code == 201
        login = client.post("/api/auth/login", json={"password": password}).json()
        headers = {"X-ZYRO-CSRF": login["csrf_token"]}
        assert (
            client.put(
                "/api/models/gemini",
                json={"api_key": "test-gemini-provider-key", "model_id": "gemini-2.5-flash"},
                headers=headers,
            ).status_code
            == 200
        )
        configured = client.put(
            "/api/research/configuration",
            json={"api_key": "test-brave-provider-key"},
            headers=headers,
        )
        assert configured.status_code == 200
        result = client.post(
            "/api/chat",
            json={"message": "Research this company and compare reliable sources"},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["task"]["status"] == "DONE"
        assert body["task"]["agent_id"] == "zyro.research"
        assert body["task"]["tools"] == ["research.web_search", "research.read_source"]
        assert len(body["task"]["result"]["sources"]) == 2
        assert all(item["digest"] for item in body["task"]["result"]["sources"])
        assert body["task"]["verification"]["outcome"] == "VERIFIED"
        organization = client.get("/api/organization").json()
        research = next(x for x in organization["departments"] if x["department_id"] == "research")
        assert research["status"] == "ACTIVE"
