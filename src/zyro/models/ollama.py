"""Local Ollama / vLLM model provider adapter for ZYRO."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from zyro.core.errors import ErrorInfo
from zyro.models.contracts import (
    ModelDefinition,
    ModelRequest,
    ModelResult,
    ModelResultStatus,
    ModelUsage,
)

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


class OllamaProvider:
    provider_id = "ollama"

    def __init__(self, host: str = DEFAULT_OLLAMA_HOST, *, timeout_seconds: float = 60.0) -> None:
        self.host = host.rstrip("/")
        self._timeout = timeout_seconds

    @property
    def available(self) -> bool:
        # Check if local Ollama daemon is reachable
        try:
            req = Request(f"{self.host}/api/tags")
            with urlopen(req, timeout=1.0) as resp:
                return bool(resp.status == 200)
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        is_avail = self.available
        return {
            "provider_id": self.provider_id,
            "display_name": "Ollama Local Models",
            "model_id": DEFAULT_OLLAMA_MODEL,
            "status": "REAL" if is_avail else "UNAVAILABLE",
            "configured": True,
            "available": is_avail,
            "capabilities": ["conversation", "coding", "local inference"],
            "host": self.host,
        }

    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        selected_model = (
            model.model_id if model.provider_id == self.provider_id else DEFAULT_OLLAMA_MODEL
        )
        payload = {
            "model": selected_model,
            "prompt": request.prompt,
            "system": request.system_instruction or "",
            "stream": False,
        }

        try:
            req = Request(
                f"{self.host}/api/generate",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode())
            content = data.get("response", "")
            usage = ModelUsage(
                input_units=data.get("prompt_eval_count", len(request.prompt) // 4),
                output_units=data.get("eval_count", len(content) // 4),
            )
            return ModelResult(
                ModelResultStatus.SUCCESS,
                request.request_id,
                request.task_id,
                request.agent_id,
                request.instance_id,
                request.correlation_id,
                self.provider_id,
                model.model_id,
                content=content,
                usage=usage,
            )
        except URLError as err:
            return ModelResult(
                ModelResultStatus.PROVIDER_UNAVAILABLE,
                request.request_id,
                request.task_id,
                request.agent_id,
                request.instance_id,
                request.correlation_id,
                self.provider_id,
                model.model_id,
                error=ErrorInfo(
                    "ollama_unavailable",
                    f"Local Ollama daemon is unreachable at {self.host}: {err.reason}",
                    "ProviderUnavailable",
                ),
            )
        except Exception as err:
            return ModelResult(
                ModelResultStatus.PROVIDER_FAILURE,
                request.request_id,
                request.task_id,
                request.agent_id,
                request.instance_id,
                request.correlation_id,
                self.provider_id,
                model.model_id,
                error=ErrorInfo("ollama_call_failed", str(err), "ProviderError"),
            )


__all__ = ["DEFAULT_OLLAMA_HOST", "DEFAULT_OLLAMA_MODEL", "OllamaProvider"]
