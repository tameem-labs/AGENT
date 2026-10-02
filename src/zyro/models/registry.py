"""Deterministic in-process catalog of non-secret model definitions."""

from __future__ import annotations

from zyro.models.contracts import ModelDefinition
from zyro.models.errors import DuplicateModelError, MissingModelError


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ModelDefinition] = {}

    def register(self, model: ModelDefinition) -> None:
        if model.model_id in self._models:
            raise DuplicateModelError(f"model is already registered: {model.model_id}")
        self._models[model.model_id] = model

    def get(self, model_id: str) -> ModelDefinition:
        try:
            return self._models[model_id]
        except KeyError as error:
            raise MissingModelError(f"model is not registered: {model_id}") from error

    def list(self) -> tuple[ModelDefinition, ...]:
        return tuple(self._models[key] for key in sorted(self._models))
