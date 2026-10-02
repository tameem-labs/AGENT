"""OpenAI model provider adapter for ZYRO."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from zyro.core.errors import ErrorInfo
from zyro.integrations import EncryptedCredentialStore
from zyro.models.contracts import (
    ModelDefinition,
    ModelRequest,
    ModelResult,
    ModelResultStatus,
    ModelUsage,
)

OPENAI_API_KEY_SECRET = "openai.api_key"
OPENAI_MODEL_SETTING = "openai.model_id"
DEFAULT_OPENAI_MODEL = "gpt-4o"


class OpenAIProvider:
    provider_id = "openai"

    def __init__(
        self, credentials: EncryptedCredentialStore, *, timeout_seconds: float = 30.0
    ) -> None:
        self._credentials = credentials
        self._timeout = timeout_seconds

    @property
    def available(self) -> bool:
        return self._credentials.has_secret(OPENAI_API_KEY_SECRET)

    @property
    def model_id(self) -> str:
        return (
            self._credentials.setting(OPENAI_MODEL_SETTING, DEFAULT_OPENAI_MODEL)
            or DEFAULT_OPENAI_MODEL
        )

    def configure(self, api_key: str, model_id: str = DEFAULT_OPENAI_MODEL) -> bool:
        clean = api_key.strip()
        if len(clean) < 16:
            return False
        self._credentials.set_secret(OPENAI_API_KEY_SECRET, clean)
        selected_model = model_id.strip() or DEFAULT_OPENAI_MODEL
        self._credentials.set_setting(OPENAI_MODEL_SETTING, selected_model)
        return True

    def remove_configuration(self) -> None:
        self._credentials.delete_secret(OPENAI_API_KEY_SECRET)

    def status(self) -> dict[str, Any]:
        configured = self.available
        return {
            "provider_id": self.provider_id,
            "display_name": "OpenAI",
            "model_id": self.model_id,
            "status": "REAL" if configured else "NOT_CONFIGURED",
            "configured": configured,
            "available": configured,
            "capabilities": ["conversation", "planning", "coding", "multimodal"],
        }

    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        if not self.available:
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
                    "openai_not_configured",
                    "OpenAI is not configured. Add an API key in Settings.",
                    "ProviderNotConfigured",
                ),
            )

        api_key = self._credentials.secret(OPENAI_API_KEY_SECRET)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages: list[dict[str, str]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model.model_id if model.provider_id == self.provider_id else self.model_id,
            "messages": messages,
            "temperature": 0.2,
        }

        try:
            req = Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers=headers,
                method="POST",
            )
            with urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode())
            choice = data["choices"][0]["message"]
            content = choice.get("content", "")
            usage_data = data.get("usage", {})
            usage = ModelUsage(
                input_units=usage_data.get("prompt_tokens", len(request.prompt) // 4),
                output_units=usage_data.get("completion_tokens", len(content) // 4),
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
        except HTTPError as err:
            err_msg = f"OpenAI API error {err.code}: {err.reason}"
            return ModelResult(
                ModelResultStatus.PROVIDER_FAILURE,
                request.request_id,
                request.task_id,
                request.agent_id,
                request.instance_id,
                request.correlation_id,
                self.provider_id,
                model.model_id,
                error=ErrorInfo("openai_http_error", err_msg, "ProviderError"),
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
                error=ErrorInfo("openai_call_failed", str(err), "ProviderError"),
            )


__all__ = ["DEFAULT_OPENAI_MODEL", "OPENAI_API_KEY_SECRET", "OpenAIProvider"]
