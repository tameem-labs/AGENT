"""Safe Windows computer control with permission gates and sandbox execution."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zyro.core.errors import ErrorInfo
from zyro.tools.contracts import ToolExecutionContext, ToolHandlerResult


class WindowsListWindowsHandler:
    """Lists visible top-level windows safely on Windows without crashing."""

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        if sys.platform != "win32":
            return ToolHandlerResult.success(
                {"windows": [], "message": "Window enumeration only available on Windows host"}
            )

        windows: list[dict[str, Any]] = []

        def enum_windows_callback(hwnd: int, extra: Any) -> bool:
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
                    title = buffer.value.strip()
                    if title:
                        windows.append({"hwnd": hwnd, "title": title})
            return True

        try:
            enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            ctypes.windll.user32.EnumWindows(enum_proc(enum_windows_callback), 0)
        except Exception as err:
            return ToolHandlerResult.failure(
                ErrorInfo("enum_windows_failed", str(err), "WindowsAPIError")
            )

        return ToolHandlerResult.success(
            {"windows": windows[:50], "total_count": len(windows)}
        )


class WindowsFocusWindowHandler:
    """Safely brings a window to the foreground by title substring."""

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        target_title = str(arguments.get("title", "")).strip().lower()
        if not target_title:
            return ToolHandlerResult.failure(
                ErrorInfo("missing_title", "title is required", "ValidationError")
            )

        if sys.platform != "win32":
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "unsupported_platform",
                    "Window focus only available on Windows host",
                    "PlatformError",
                )
            )

        found_hwnd: int | None = None

        def enum_windows_callback(hwnd: int, extra: Any) -> bool:
            nonlocal found_hwnd
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
                    title = buffer.value.strip().lower()
                    if target_title in title:
                        found_hwnd = hwnd
                        return False
            return True

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        ctypes.windll.user32.EnumWindows(enum_proc(enum_windows_callback), 0)

        if found_hwnd is None:
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "window_not_found",
                    f"No window found matching '{target_title}'",
                    "NotFoundError",
                )
            )

        ctypes.windll.user32.SetForegroundWindow(found_hwnd)
        return ToolHandlerResult.success(
            {"focused_hwnd": found_hwnd, "status": "FOCUSED"}
        )


class WindowsSandboxedExecHandler:
    """Controlled terminal command execution confined to workspace sandbox."""

    ALLOWED_PROGRAMS = frozenset({"python", "git", "pytest", "ruff", "mypy", "node", "npm"})

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path.cwd()

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        program = str(arguments.get("program", "")).strip()
        args = arguments.get("args", [])
        if not isinstance(args, list):
            return ToolHandlerResult.failure(
                ErrorInfo("invalid_args", "args must be a list of strings", "ValidationError")
            )

        if program not in self.ALLOWED_PROGRAMS:
            allowed = sorted(self.ALLOWED_PROGRAMS)
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "program_denied",
                    f"Program '{program}' not allowed. Allowed: {allowed}",
                    "SecurityError",
                )
            )

        for arg in args:
            if any(char in str(arg) for char in (";", "&", "|", "`", "$", "\n")):
                return ToolHandlerResult.failure(
                    ErrorInfo(
                        "injection_denied",
                        f"Disallowed character in argument: {arg}",
                        "SecurityError",
                    )
                )

        safe_env = {
            k: v
            for k, v in os.environ.items()
            if not any(
                secret_word in k.upper()
                for secret_word in ("SECRET", "KEY", "TOKEN", "PASSWORD", "AUTH")
            )
        }

        full_cmd = [program] + [str(a) for a in args]
        start = time.monotonic()
        timeout = float(arguments.get("timeout_seconds", 30))
        try:
            completed = subprocess.run(
                full_cmd,
                cwd=str(self.workspace_root),
                env=safe_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return ToolHandlerResult.success(
                {
                    "program": program,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[:4000],
                    "stderr": completed.stderr[:4000],
                    "duration_ms": duration_ms,
                    "passed": completed.returncode == 0,
                }
            )
        except subprocess.TimeoutExpired:
            return ToolHandlerResult.failure(
                ErrorInfo("timeout", f"Execution timed out after {timeout}s", "TimeoutError")
            )
        except Exception as ex:
            return ToolHandlerResult.failure(
                ErrorInfo("execution_failed", str(ex), "ExecutionError")
            )


__all__ = [
    "WindowsFocusWindowHandler",
    "WindowsListWindowsHandler",
    "WindowsSandboxedExecHandler",
]
