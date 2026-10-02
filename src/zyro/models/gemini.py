"""Google Gemini REST adapter with encrypted-key lookup and structured failures."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from zyro.core.errors import ErrorInfo
from zyro.integrations.store import EncryptedCredentialStore
from zyro.models.contracts import (
    ModelDefinition,
    ModelRequest,
    ModelResult,
    ModelResultStatus,
    ModelUsage,
)

GEMINI_API_KEY_SECRET = "models.gemini.api_key"
GEMINI_MODEL_SETTING = "models.gemini.default_model"
GEMINI_VALIDATED_SETTING = "models.gemini.validated"
GEMINI_API_KEY_URL = "https://aistudio.google.com/app/apikey"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class GeminiTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]: ...


class UrllibGeminiTransport:
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]:
        encoded = None if payload is None else json.dumps(payload).encode()
        response: HTTPResponse
        with urlopen(Request(url, encoded, headers, method=method), timeout=timeout) as response:
            value = json.loads(response.read())
        if not isinstance(value, dict):
            raise ValueError("provider response is not an object")
        return cast(dict[str, Any], value)


@dataclass(frozen=True, slots=True)
class GeminiValidation:
    valid: bool
    status: str
    message: str


class GeminiProvider:
    provider_id = "google.gemini"

    def __init__(
        self,
        credentials: EncryptedCredentialStore,
        transport: GeminiTransport | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._credentials = credentials
        self._transport = transport or UrllibGeminiTransport()
        self._timeout = timeout_seconds

    @property
    def available(self) -> bool:
        return (
            self._credentials.has_secret(GEMINI_API_KEY_SECRET)
            and self._credentials.setting(GEMINI_VALIDATED_SETTING, "false") == "true"
        )

    @property
    def model_id(self) -> str:
        return (
            self._credentials.setting(GEMINI_MODEL_SETTING, DEFAULT_GEMINI_MODEL)
            or DEFAULT_GEMINI_MODEL
        )

    def configure(self, api_key: str, model_id: str = DEFAULT_GEMINI_MODEL) -> GeminiValidation:
        clean_key = api_key.strip()
        clean_model = model_id.strip()
        if (
            len(clean_key) < 16
            or clean_model != DEFAULT_GEMINI_MODEL
            or any(char.isspace() for char in clean_model)
        ):
            return GeminiValidation(
                False, "INVALID", "The API key is invalid or the model is not supported."
            )
        validation = self.validate_key(clean_key, clean_model)
        if validation.valid:
            self._credentials.set_secret(GEMINI_API_KEY_SECRET, clean_key)
            self._credentials.set_setting(GEMINI_MODEL_SETTING, clean_model)
            self._credentials.set_setting(GEMINI_VALIDATED_SETTING, "true")
        return validation

    def validate_configured(self) -> GeminiValidation:
        if not self.available:
            return GeminiValidation(False, "NOT_CONFIGURED", "Gemini is not configured.")
        validation = self.validate_key(
            self._credentials.secret(GEMINI_API_KEY_SECRET), self.model_id
        )
        self._credentials.set_setting(
            GEMINI_VALIDATED_SETTING, "true" if validation.valid else "false"
        )
        return validation

    def validate_key(self, api_key: str, model_id: str) -> GeminiValidation:
        try:
            self._transport.request(
                "GET",
                f"{GEMINI_API_BASE}/models/{model_id}",
                {"x-goog-api-key": api_key, "Accept": "application/json"},
                None,
                self._timeout,
            )
            return GeminiValidation(True, "CONFIGURED", "Gemini key and model are available.")
        except Exception as error:
            info = self._classify(error)
            return GeminiValidation(False, self._configuration_status(error), info.message)

    def remove(self) -> None:
        self._credentials.delete_secret(GEMINI_API_KEY_SECRET)
        self._credentials.set_setting(GEMINI_VALIDATED_SETTING, "false")

    def status(self) -> dict[str, Any]:
        configured = self.available
        validated = self._credentials.setting(GEMINI_VALIDATED_SETTING, "false") == "true"
        return {
            "provider_id": self.provider_id,
            "display_name": "Google Gemini",
            "model_id": self.model_id,
            "status": "CONFIGURED"
            if configured and validated
            else ("INVALID" if configured else "NOT_CONFIGURED"),
            "configured": configured,
            "available": configured and validated,
            "capabilities": ["conversation", "planning", "structured output", "tool calling"],
            "api_key_url": GEMINI_API_KEY_URL,
        }

    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        if not self.available:
            return self._failure(
                model,
                request,
                ModelResultStatus.PROVIDER_UNAVAILABLE,
                ErrorInfo(
                    "gemini_not_configured",
                    "Gemini is not configured. Add an API key in Settings.",
                    "ProviderNotConfigured",
                ),
            )
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048},
        }
        if request.system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": request.system_instruction}]}
        try:
            body = self._transport.request(
                "POST",
                f"{GEMINI_API_BASE}/models/{model.model_id}:generateContent",
                {
                    "x-goog-api-key": self._credentials.secret(GEMINI_API_KEY_SECRET),
                    "Content-Type": "application/json",
                },
                payload,
                self._timeout,
            )
            candidates = body.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise ValueError("response has no candidates")
            content = candidates[0].get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []
            text = "".join(
                str(part.get("text", "")) for part in parts if isinstance(part, dict)
            ).strip()
            if not text:
                raise ValueError("response has no text")
            usage = body.get("usageMetadata", {})
            return ModelResult(
                ModelResultStatus.SUCCESS,
                request.request_id,
                request.task_id,
                request.agent_id,
                request.instance_id,
                request.correlation_id,
                self.provider_id,
                model.model_id,
                content=text,
                usage=ModelUsage(
                    int(usage.get("promptTokenCount", 0)),
                    int(usage.get("candidatesTokenCount", 0)),
                ),
            )
        except ValueError as error:
            return self._failure(
                model,
                request,
                ModelResultStatus.MALFORMED_RESPONSE,
                ErrorInfo(
                    "gemini_malformed_response",
                    "Gemini returned a malformed or empty response.",
                    type(error).__name__,
                    True,
                ),
            )
        except Exception as error:
            info = self._classify(error)
            status = (
                ModelResultStatus.TIMEOUT
                if info.error_type == "ProviderTimeout"
                else ModelResultStatus.PROVIDER_FAILURE
            )
            return self._failure(model, request, status, info)

    @staticmethod
    def _configuration_status(error: Exception) -> str:
        if isinstance(error, HTTPError) and error.code in {401, 403}:
            return "INVALID"
        if isinstance(error, HTTPError) and error.code == 404:
            return "UNAVAILABLE"
        return "UNAVAILABLE"

    @staticmethod
    def _classify(error: Exception) -> ErrorInfo:
        if isinstance(error, HTTPError):
            if error.code in {401, 403}:
                return ErrorInfo(
                    "gemini_invalid_api_key",
                    "Gemini rejected the API key or its project permissions.",
                    "InvalidProviderCredential",
                )
            if error.code == 404:
                return ErrorInfo(
                    "gemini_model_unavailable",
                    "The configured Gemini model is unavailable.",
                    "ModelUnavailable",
                    True,
                )
            if error.code == 429:
                return ErrorInfo(
                    "gemini_rate_limited",
                    "Gemini quota or rate limit was reached.",
                    "ProviderRateLimit",
                    True,
                )
            if error.code in {400, 413}:
                return ErrorInfo(
                    "gemini_request_rejected",
                    "Gemini rejected the request, possibly because of its context limit.",
                    "ProviderRequestRejected",
                )
            return ErrorInfo(
                "gemini_provider_error",
                f"Gemini returned HTTP {error.code}.",
                "ProviderError",
                error.code >= 500,
            )
        if isinstance(error, (TimeoutError, socket.timeout)) or (
            isinstance(error, URLError) and isinstance(error.reason, socket.timeout)
        ):
            return ErrorInfo(
                "gemini_timeout",
                "Gemini did not respond before the configured timeout.",
                "ProviderTimeout",
                True,
            )
        if isinstance(error, URLError):
            return ErrorInfo(
                "gemini_network_failure",
                "Gemini could not be reached from this machine.",
                "ProviderNetworkFailure",
                True,
            )
        return ErrorInfo(
            "gemini_provider_failure",
            "Gemini failed without returning a usable response.",
            type(error).__name__,
            True,
        )

    @staticmethod
    def _failure(
        model: ModelDefinition,
        request: ModelRequest,
        status: ModelResultStatus,
        error: ErrorInfo,
    ) -> ModelResult:
        return ModelResult(
            status,
            request.request_id,
            request.task_id,
            request.agent_id,
            request.instance_id,
            request.correlation_id,
            GeminiProvider.provider_id,
            model.model_id,
            error=error,
        )


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "GEMINI_API_KEY_SECRET",
    "GEMINI_API_KEY_URL",
    "GeminiProvider",
    "GeminiTransport",
    "GeminiValidation",
    "UrllibGeminiTransport",
]
