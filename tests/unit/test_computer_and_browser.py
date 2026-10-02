"""Unit tests for browser automation, SSRF defenses, and computer control."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zyro.computer import (
    BrowserNavigateHandler,
    ScreenCaptureHandler,
    ScreenFrame,
    WindowsFocusWindowHandler,
    WindowsListWindowsHandler,
    validate_browser_url,
)
from zyro.tools.contracts import ToolExecutionContext


def test_browser_ssrf_validation() -> None:
    # Disallowed scheme
    assert validate_browser_url("ftp://example.com") is not None
    assert validate_browser_url("file:///etc/passwd") is not None

    # Localhost and link-local blocked
    assert validate_browser_url("http://localhost:8000/api") is not None
    assert validate_browser_url("http://127.0.0.1/admin") is not None
    assert validate_browser_url("http://169.254.169.254/latest/meta-data") is not None

    # Private IPv4 blocked
    assert validate_browser_url("http://192.168.1.1/router") is not None
    assert validate_browser_url("http://10.0.0.1/secret") is not None
    assert validate_browser_url("http://172.16.0.1/intranet") is not None

    # Public URLs allowed
    assert validate_browser_url("https://example.com/blog") is None
    assert validate_browser_url("http://python.org") is None


def test_browser_navigate_handler_ssrf_block() -> None:
    handler = BrowserNavigateHandler()
    context = ToolExecutionContext(
        tool_id="computer.browse",
        request_id="req-1",
        task_id="task-1",
        agent_id="zyro.executive",
        instance_id="inst-1",
        correlation_id="corr-1",
    )
    result = handler.execute(context, {"url": "http://127.0.0.1:8080/private"})
    assert not result.succeeded
    assert result.error is not None
    assert result.error.error_type == "SecurityError"
    assert "ssrf" in result.error.message.lower()


def test_screen_frame_staleness_detection() -> None:
    now = datetime.now(UTC)
    fresh_frame = ScreenFrame(
        frame_id="frame-1",
        captured_at=now,
        width=1920,
        height=1080,
        digest="sha256-digest-1",
    )
    assert not fresh_frame.is_stale(max_age_seconds=5.0)

    stale_frame = ScreenFrame(
        frame_id="frame-2",
        captured_at=now - timedelta(seconds=10),
        width=1920,
        height=1080,
        digest="sha256-digest-2",
    )
    assert stale_frame.is_stale(max_age_seconds=5.0)


def test_screen_capture_handler() -> None:
    handler = ScreenCaptureHandler()
    context = ToolExecutionContext(
        tool_id="screen.capture",
        request_id="req-1",
        task_id="task-1",
        agent_id="zyro.executive",
        instance_id="inst-1",
        correlation_id="corr-1",
    )
    result = handler.execute(context, {})
    assert result.succeeded
    assert result.output is not None
    assert "frame" in result.output
    assert result.output["frame"]["width"] == 1920
    assert result.output["frame"]["is_masked"] is True


def test_windows_controls_safe_execution() -> None:
    list_handler = WindowsListWindowsHandler()
    focus_handler = WindowsFocusWindowHandler()
    context = ToolExecutionContext(
        tool_id="computer.list_windows",
        request_id="req-1",
        task_id="task-1",
        agent_id="zyro.executive",
        instance_id="inst-1",
        correlation_id="corr-1",
    )
    list_res = list_handler.execute(context, {})
    assert list_res.succeeded
    assert list_res.output is not None
    assert "windows" in list_res.output

    # Missing title validation
    focus_res = focus_handler.execute(context, {})
    assert not focus_res.succeeded
    assert focus_res.error is not None
    assert focus_res.error.error_type == "ValidationError"
