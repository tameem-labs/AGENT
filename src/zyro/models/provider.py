"""Replaceable model-provider contract and deterministic in-process registry."""

from __future__ import annotations

from typing import Protocol

from zyro.models.contracts import ModelDefinition, ModelRequest, ModelResult
from zyro.models.errors import (
    DuplicateProviderError,
    InvalidProviderError,
    MissingProviderError,
)


class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str:
        """Stable non-secret provider identity."""
        ...

    @property
    def available(self) -> bool:
        """Whether this adapter can currently accept an invocation."""
        ...

    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        """Invoke exactly one selected model and return a structured result."""
        ...


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        provider_id = provider.provider_id
        if (
            not isinstance(provider_id, str)
            or not provider_id.strip()
            or provider_id != provider_id.strip()
        ):
            raise InvalidProviderError("provider_id must be a normalized non-empty string")
        if provider_id in self._providers:
            raise DuplicateProviderError(f"provider is already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> ModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise MissingProviderError(f"provider is not registered: {provider_id}") from error

    def list(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))
