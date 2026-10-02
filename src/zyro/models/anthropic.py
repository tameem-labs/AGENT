"""Anthropic Claude model provider adapter for ZYRO."""

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

ANTHROPIC_API_KEY_SECRET = "anthropic.api_key"
ANTHROPIC_MODEL_SETTING = "anthropic.model_id"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-20241022"


class AnthropicProvider:
    provider_id = "anthropic"

    def __init__(
        self, credentials: EncryptedCredentialStore, *, timeout_seconds: float = 30.0
    ) -> None:
        self._credentials = credentials
        self._timeout = timeout_seconds

    @property
    def available(self) -> bool:
        return self._credentials.has_secret(ANTHROPIC_API_KEY_SECRET)

    @property
    def model_id(self) -> str:
        return (
            self._credentials.setting(ANTHROPIC_MODEL_SETTING, DEFAULT_ANTHROPIC_MODEL)
            or DEFAULT_ANTHROPIC_MODEL
        )

    def configure(self, api_key: str, model_id: str = DEFAULT_ANTHROPIC_MODEL) -> bool:
        clean = api_key.strip()
        if len(clean) < 16:
            return False
        self._credentials.set_secret(ANTHROPIC_API_KEY_SECRET, clean)
        self._credentials.set_setting(
            ANTHROPIC_MODEL_SETTING, model_id.strip() or DEFAULT_ANTHROPIC_MODEL
        )
        return True

    def remove_configuration(self) -> None:
        self._credentials.delete_secret(ANTHROPIC_API_KEY_SECRET)

    def status(self) -> dict[str, Any]:
        configured = self.available
        return {
            "provider_id": self.provider_id,
            "display_name": "Anthropic Claude",
            "model_id": self.model_id,
            "status": "REAL" if configured else "NOT_CONFIGURED",
            "configured": configured,
            "available": configured,
            "capabilities": ["conversation", "planning", "coding", "reasoning"],
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
                    "anthropic_not_configured",
                    "Anthropic is not configured. Add an API key in Settings.",
                    "ProviderNotConfigured",
                ),
            )

        api_key = self._credentials.secret(ANTHROPIC_API_KEY_SECRET)
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        messages = [{"role": "user", "content": request.prompt}]

        payload: dict[str, Any] = {
            "model": model.model_id if model.provider_id == self.provider_id else self.model_id,
            "max_tokens": 4096,
            "messages": messages,
        }
        if request.system_instruction:
            payload["system"] = request.system_instruction

        try:
            req = Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode(),
                headers=headers,
                method="POST",
            )
            with urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode())
            content_blocks = data.get("content", [])
            content = content_blocks[0].get("text", "") if content_blocks else ""
            usage_data = data.get("usage", {})
            usage = ModelUsage(
                input_units=usage_data.get("input_tokens", len(request.prompt) // 4),
                output_units=usage_data.get("output_tokens", len(content) // 4),
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
            return ModelResult(
                ModelResultStatus.PROVIDER_FAILURE,
                request.request_id,
                request.task_id,
                request.agent_id,
                request.instance_id,
                request.correlation_id,
                self.provider_id,
                model.model_id,
                error=ErrorInfo(
                    "anthropic_http_error",
                    f"Anthropic API error {err.code}: {err.reason}",
                    "ProviderError",
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
                error=ErrorInfo("anthropic_call_failed", str(err), "ProviderError"),
            )


__all__ = ["ANTHROPIC_API_KEY_SECRET", "DEFAULT_ANTHROPIC_MODEL", "AnthropicProvider"]
