from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from zyro.core.errors import ErrorInfo
from zyro.core.logging import JsonFormatter
from zyro.models.contracts import (
    ModelRequest,
    ModelRequirements,
    ModelResult,
    ModelResultStatus,
    ModelUsage,
)
from zyro.observability import (
    ObservedModelInvoker,
    ObservedToolInvoker,
    OperationalObserver,
    SQLiteObservabilityStore,
    TraceQuery,
    TraceRecord,
    TraceStatus,
)
from zyro.tools.contracts import ToolCall, ToolResult, ToolResultStatus

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def record(trace_id: str = "trace-1") -> TraceRecord:
    return TraceRecord(
        trace_id,
        "TOOL_CALLED",
        NOW,
        "tool",
        "execute",
        TraceStatus.SUCCEEDED,
        "request-1",
        "task-1",
        "correlation-1",
        workflow_id="workflow-1",
        agent_id="agent-1",
        instance_id="instance-1",
        tool_id="tool-1",
        model_id="model-1",
        approval_id="approval-1",
        verification_id="verification-1",
        recovery_id="recovery-1",
        duration_ms=12.5,
        attempt=2,
        resource_usage={"tokens": 10},
        metadata={"result": "bounded"},
    )


def test_trace_persistence_query_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "observability.sqlite"
    store = SQLiteObservabilityStore(path)

    assert store.append(record())
    assert not store.append(record())
    by_correlation = store.query(TraceQuery(correlation_id="correlation-1"))
    by_recovery = store.query(TraceQuery(recovery_id="recovery-1"))

    assert by_correlation == (record(),)
    assert by_recovery[0].workflow_id == "workflow-1"
    assert by_recovery[0].approval_id == "approval-1"
    store.close()

    reopened = SQLiteObservabilityStore(path)
    assert reopened.query(TraceQuery(task_id="task-1")) == (record(),)
    reopened.close()


def test_trace_redacts_secret_shaped_metadata_before_persistence(tmp_path: Path) -> None:
    path = tmp_path / "redaction.sqlite"
    store = SQLiteObservabilityStore(path)
    secret_key = "access" + "_token"
    source = record("trace-redacted")
    source = TraceRecord(
        **{
            field: getattr(source, field)
            for field in source.__dataclass_fields__
            if field not in {"trace_id", "metadata"}
        },
        trace_id="trace-redacted",
        metadata={secret_key: "redacted-fixture", "message": "password" + "=redacted"},
    )

    store.append(source)
    restored = store.query(TraceQuery(task_id="task-1"))[0]
    encoded = json.dumps(dict(restored.metadata))

    assert "redacted-fixture" not in encoded
    assert encoded.count("[REDACTED]") == 2
    store.close()


def test_json_logging_emits_structured_fields_and_sanitizes_exception() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("observability-test")
    logger.handlers = [handler]
    logger.propagate = False
    try:
        raise RuntimeError("password" + "=redacted")
    except RuntimeError:
        logger.exception(
            "operation failed",
            extra={
                "event_type": "EXECUTION_FAILED",
                "component": "runtime",
                "resource_usage": {"access" + "_token": "redacted-fixture"},
            },
        )

    payload = json.loads(stream.getvalue())
    assert payload["event_type"] == "EXECUTION_FAILED"
    assert payload["resource_usage"]["access_token"] == "[REDACTED]"
    assert payload["exception_type"] == "RuntimeError"
    assert "redacted-fixture" not in stream.getvalue()


class SuccessfulModel:
    def invoke(self, request: ModelRequest) -> ModelResult:
        return ModelResult(
            ModelResultStatus.SUCCESS,
            request.request_id,
            request.task_id,
            request.agent_id,
            request.instance_id,
            request.correlation_id,
            "provider-1",
            "model-1",
            content="ok",
            usage=ModelUsage(5, 3),
        )


def test_model_adapter_preserves_behavior_and_writes_coherent_trace(tmp_path: Path) -> None:
    store = SQLiteObservabilityStore(tmp_path / "adapter.sqlite")
    sequence = iter(("trace-start", "trace-complete"))
    observer = OperationalObserver(store, clock=lambda: NOW, id_factory=lambda: next(sequence))
    adapter = ObservedModelInvoker(SuccessfulModel(), observer)
    request = ModelRequest(
        "Prompt",
        ModelRequirements("test"),
        "request-1",
        "task-1",
        "agent-1",
        "instance-1",
        "correlation-1",
    )

    result = adapter.invoke(request)
    trace = store.query(TraceQuery(correlation_id="correlation-1"))

    assert result.succeeded
    assert [item.event_type for item in trace] == [
        "MODEL_INVOCATION_STARTED",
        "MODEL_INVOCATION_COMPLETED",
    ]
    assert trace[1].model_id == "model-1"
    assert trace[1].duration_ms is not None and trace[1].duration_ms >= 0
    assert trace[1].resource_usage == {
        "input_units": 5,
        "output_units": 3,
        "precision": "EXACT",
    }
    store.close()


class UnknownTool:
    def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult(
            ToolResultStatus.UNKNOWN,
            call.tool_id,
            call.request_id,
            call.task_id,
            call.agent_id,
            call.instance_id,
            call.correlation_id,
            error=ErrorInfo("uncertain", "outcome unknown", "EXTERNAL_SIDE_EFFECT_UNCERTAINTY"),
            approval_id=call.approval_id,
        )


def test_tool_adapter_preserves_uncertainty_and_approval_correlation(tmp_path: Path) -> None:
    store = SQLiteObservabilityStore(tmp_path / "tool-adapter.sqlite")
    sequence = iter(("trace-start", "trace-complete"))
    observer = OperationalObserver(store, clock=lambda: NOW, id_factory=lambda: next(sequence))
    adapter = ObservedToolInvoker(UnknownTool(), observer)
    call = ToolCall(
        "tool-1",
        {},
        "request-1",
        "task-1",
        "agent-1",
        "instance-1",
        "correlation-1",
        approval_id="approval-1",
    )

    result = adapter.execute(call)
    trace = store.query(TraceQuery(correlation_id="correlation-1"))

    assert result.outcome_unknown
    assert trace[-1].status is TraceStatus.UNKNOWN
    assert trace[-1].approval_id == "approval-1"
    assert trace[-1].duration_ms is not None and trace[-1].duration_ms >= 0
    assert trace[-1].error_classification == "EXTERNAL_SIDE_EFFECT_UNCERTAINTY"
    store.close()


def test_tool_adapter_records_delegate_exception_and_preserves_it(tmp_path: Path) -> None:
    class ExplodingTool:
        def execute(self, call: ToolCall) -> ToolResult:
            raise TimeoutError("sensitive delegate detail")

    store = SQLiteObservabilityStore(tmp_path / "tool-error.sqlite")
    sequence = iter(("trace-start", "trace-complete"))
    observer = OperationalObserver(store, clock=lambda: NOW, id_factory=lambda: next(sequence))
    adapter = ObservedToolInvoker(ExplodingTool(), observer)
    call = ToolCall("tool-1", {}, "request-1", "task-1", "agent-1", "instance-1", "correlation-1")

    with pytest.raises(TimeoutError, match="sensitive delegate detail"):
        adapter.execute(call)

    trace = store.query(TraceQuery(correlation_id="correlation-1"))
    assert trace[-1].status is TraceStatus.FAILED
    assert trace[-1].error_classification == "TimeoutError"
    assert "sensitive delegate detail" not in json.dumps(dict(trace[-1].metadata))
    store.close()


def test_trace_metadata_is_bounded() -> None:
    source = record("trace-oversized")
    values = {
        field: getattr(source, field)
        for field in source.__dataclass_fields__
        if field not in {"trace_id", "metadata"}
    }
    with pytest.raises(ValueError, match="telemetry size limit"):
        TraceRecord(**values, trace_id="trace-oversized", metadata={"value": "x" * 20_000})


def test_trace_query_cannot_dump_all_records() -> None:
    with pytest.raises(ValueError):
        TraceQuery()
