import json
from io import StringIO

from zyro.core.logging import LogContext, configure_logging, get_logger


def test_structured_log_contains_correlation_fields() -> None:
    output = StringIO()
    configure_logging("INFO", output)
    logger = get_logger(
        "test",
        LogContext(request_id="req-1", task_id="task-1", correlation_id="corr-1"),
    )

    logger.info("foundation check")

    record = json.loads(output.getvalue())
    assert record["message"] == "foundation check"
    assert record["request_id"] == "req-1"
    assert record["task_id"] == "task-1"
    assert record["correlation_id"] == "corr-1"
    assert "workflow_id" not in record
