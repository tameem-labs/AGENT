"""Canonical Resource Manager adapters for model and tool runtime paths."""

from __future__ import annotations

from zyro.core.errors import ErrorInfo
from zyro.models.contracts import ModelInvoker, ModelRequest, ModelResult, ModelResultStatus
from zyro.resources.contracts import ConsumptionOutcome, UsagePrecision
from zyro.resources.manager import SQLiteResourceManager


class ResourceAwareModelInvoker:
    """Enforce Task/Workflow budgets around every model invocation."""

    def __init__(self, delegate: ModelInvoker, resources: SQLiteResourceManager) -> None:
        self._delegate = delegate
        self._resources = resources

    def invoke(self, request: ModelRequest) -> ModelResult:
        # A deterministic conservative input estimate is admitted before provider work.
        input_estimate = max(
            1, (len(request.prompt) + len(request.system_instruction or "") + 3) // 4
        )
        admitted = self._resources.consume_tokens(
            request.task_id,
            request.workflow_id,
            input_estimate,
            UsagePrecision.ESTIMATED,
            agent_id=request.agent_id,
            consumer_id="model-input",
        )
        if admitted.outcome is ConsumptionOutcome.RESOURCE_LIMIT_REACHED:
            return self._failure(request, "model_task_budget_exhausted")
        result = self._delegate.invoke(request)
        reported_input = result.usage.input_units
        output = result.usage.output_units
        # The admitted estimate already covers input. Add any positive provider delta.
        additional_input = max(0, reported_input - input_estimate)
        if result.succeeded and reported_input + output == 0:
            self._resources.consume_tokens(
                request.task_id,
                request.workflow_id,
                None,
                UsagePrecision.UNKNOWN,
                agent_id=request.agent_id,
                consumer_id=result.provider_id,
            )
            return self._failure(request, "model_usage_unknown", retryable=False)
        if additional_input + output:
            consumed = self._resources.consume_tokens(
                request.task_id,
                request.workflow_id,
                additional_input + output,
                UsagePrecision.EXACT,
                agent_id=request.agent_id,
                consumer_id=result.provider_id,
            )
            if consumed.outcome is ConsumptionOutcome.RESOURCE_LIMIT_REACHED:
                return self._failure(request, "model_task_budget_exhausted")
        return result

    @staticmethod
    def _failure(request: ModelRequest, code: str, *, retryable: bool = False) -> ModelResult:
        return ModelResult(
            ModelResultStatus.PROVIDER_FAILURE,
            request.request_id,
            request.task_id,
            request.agent_id,
            request.instance_id,
            request.correlation_id,
            error=ErrorInfo(
                code,
                "Model invocation was stopped because resource usage could not be safely admitted.",
                "ResourceExhaustion",
                retryable=retryable,
            ),
        )


__all__ = ["ResourceAwareModelInvoker"]
