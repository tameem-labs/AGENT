"""Deterministic test-only providers and tools; never packaged as product adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from zyro.core.errors import ErrorInfo
from zyro.models.contracts import (
    ModelDefinition,
    ModelRequest,
    ModelResult,
    ModelResultStatus,
    RequestedToolCall,
)
from zyro.tools.contracts import ToolExecutionContext, ToolHandlerResult


@dataclass
class DeterministicModelProvider:
    provider_id: str = "test-provider"
    available: bool = True
    content: str = "deterministic model output"
    tool_calls: tuple[RequestedToolCall, ...] = ()
    failure: ErrorInfo | None = None
    requests: list[ModelRequest] = field(default_factory=list)

    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        if self.failure is not None:
            return ModelResult(
                status=ModelResultStatus.PROVIDER_FAILURE,
                request_id=request.request_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
                instance_id=request.instance_id,
                correlation_id=request.correlation_id,
                provider_id=self.provider_id,
                model_id=model.model_id,
                error=self.failure,
            )
        return ModelResult(
            status=ModelResultStatus.SUCCESS,
            request_id=request.request_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            instance_id=request.instance_id,
            correlation_id=request.correlation_id,
            provider_id=self.provider_id,
            model_id=model.model_id,
            content=self.content,
            requested_tool_calls=self.tool_calls,
        )


@dataclass
class EchoToolHandler:
    calls: list[ToolExecutionContext] = field(default_factory=list)

    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        self.calls.append(context)
        return ToolHandlerResult.success({"text": arguments["text"]})
