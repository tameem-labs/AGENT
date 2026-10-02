"""Composable model/tool telemetry adapters without changing their authority."""

from __future__ import annotations

from time import perf_counter

from zyro.models.contracts import ModelInvoker, ModelRequest, ModelResult
from zyro.observability.contracts import TraceStatus
from zyro.observability.service import Observer, TraceContext
from zyro.tools.contracts import ToolCall, ToolInvoker, ToolResult


def _record(
    observer: Observer,
    event_type: str,
    component: str,
    operation: str,
    status: TraceStatus,
    context: TraceContext,
    **fields: object,
) -> None:
    try:
        observer.record(event_type, component, operation, status, context, **fields)
    except Exception:
        # Telemetry failure cannot alter execution behavior.
        return


class ObservedModelInvoker:
    def __init__(self, delegate: ModelInvoker, observer: Observer) -> None:
        self._delegate = delegate
        self._observer = observer

    def invoke(self, request: ModelRequest) -> ModelResult:
        context = TraceContext(
            request.request_id,
            request.task_id,
            request.correlation_id,
            agent_id=request.agent_id,
            instance_id=request.instance_id,
        )
        _record(
            self._observer,
            "MODEL_INVOCATION_STARTED",
            "model",
            "invoke",
            TraceStatus.STARTED,
            context,
        )
        started = perf_counter()
        try:
            result = self._delegate.invoke(request)
        except Exception as error:
            _record(
                self._observer,
                "MODEL_INVOCATION_COMPLETED",
                "model",
                "invoke",
                TraceStatus.FAILED,
                context,
                duration_ms=(perf_counter() - started) * 1000,
                error_classification=type(error).__name__,
            )
            raise
        _record(
            self._observer,
            "MODEL_INVOCATION_COMPLETED",
            "model",
            "invoke",
            TraceStatus.SUCCEEDED if result.succeeded else TraceStatus.FAILED,
            context,
            model_id=result.model_id,
            duration_ms=(perf_counter() - started) * 1000,
            error_classification=None if result.error is None else result.error.error_type,
            resource_usage={
                "input_units": result.usage.input_units,
                "output_units": result.usage.output_units,
                "precision": "EXACT",
            },
        )
        return result


class ObservedToolInvoker:
    def __init__(self, delegate: ToolInvoker, observer: Observer) -> None:
        self._delegate = delegate
        self._observer = observer

    def execute(self, call: ToolCall) -> ToolResult:
        context = TraceContext(
            call.request_id,
            call.task_id,
            call.correlation_id,
            agent_id=call.agent_id,
            instance_id=call.instance_id,
        )
        _record(
            self._observer,
            "TOOL_CALL_STARTED",
            "tool",
            "execute",
            TraceStatus.STARTED,
            context,
            tool_id=call.tool_id,
            approval_id=call.approval_id,
        )
        started = perf_counter()
        try:
            result = self._delegate.execute(call)
        except Exception as error:
            _record(
                self._observer,
                "TOOL_CALL_COMPLETED",
                "tool",
                "execute",
                TraceStatus.FAILED,
                context,
                tool_id=call.tool_id,
                approval_id=call.approval_id,
                duration_ms=(perf_counter() - started) * 1000,
                error_classification=type(error).__name__,
            )
            raise
        status = (
            TraceStatus.SUCCEEDED
            if result.succeeded
            else (TraceStatus.UNKNOWN if result.outcome_unknown else TraceStatus.FAILED)
        )
        _record(
            self._observer,
            "TOOL_CALL_COMPLETED",
            "tool",
            "execute",
            status,
            context,
            tool_id=result.tool_id,
            approval_id=result.approval_id,
            duration_ms=(perf_counter() - started) * 1000,
            error_classification=None if result.error is None else result.error.error_type,
        )
        return result


__all__ = ["ObservedModelInvoker", "ObservedToolInvoker"]
