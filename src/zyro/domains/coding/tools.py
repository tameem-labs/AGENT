"""Sandboxed tools for the Coding Department."""

from __future__ import annotations

import ast
import os
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zyro.core.errors import ErrorInfo
from zyro.domains.coding.contracts import (
    ASTAnalysisResult,
    ASTSymbol,
    CodeProject,
    TestRunResult,
)
from zyro.tools.contracts import ToolExecutionContext, ToolHandlerResult


class CodingProjectDiscoveryHandler:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path.cwd()

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        target = Path(arguments.get("path", self.workspace_root)).resolve()
        if not target.is_dir():
            return ToolHandlerResult.failure(
                ErrorInfo("invalid_path", f"Path is not a directory: {target}", "ValidationError")
            )

        has_git = (target / ".git").is_dir()
        language = "unknown"
        if (target / "pyproject.toml").is_file() or any(target.glob("*.py")):
            language = "python"
        elif (target / "package.json").is_file():
            language = "typescript/javascript"
        elif (target / "Cargo.toml").is_file():
            language = "rust"
        elif (target / "go.mod").is_file():
            language = "go"

        project = CodeProject(
            project_path=str(target),
            name=target.name,
            language=language,
            has_git=has_git,
        )
        return ToolHandlerResult.success(
            {
                "project_path": project.project_path,
                "name": project.name,
                "language": project.language,
                "has_git": project.has_git,
            }
        )


class ASTAnalysisHandler:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path.cwd()

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        path_str = arguments.get("file_path", "")
        if not path_str:
            return ToolHandlerResult.failure(
                ErrorInfo("missing_file", "file_path is required", "ValidationError")
            )
        target = (self.workspace_root / str(path_str)).resolve()
        try:
            target.relative_to(self.workspace_root.resolve())
        except ValueError:
            return ToolHandlerResult.failure(
                ErrorInfo("sandbox_escape", "Path escapes workspace sandbox", "SecurityError")
            )

        if not target.is_file():
            return ToolHandlerResult.failure(
                ErrorInfo("file_not_found", f"File not found: {path_str}", "NotFoundError")
            )

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=str(target))
        except SyntaxError as err:
            result = ASTAnalysisResult(
                symbols=(),
                import_modules=(),
                syntax_valid=False,
                error=f"SyntaxError at line {err.lineno}: {err.msg}",
            )
            return ToolHandlerResult.success(result.to_dict())

        symbols: list[ASTSymbol] = []
        import_modules: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append(ASTSymbol("class", node.name, node.lineno))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                symbols.append(ASTSymbol("function", node.name, node.lineno))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    import_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                import_modules.append(node.module)

        analysis = ASTAnalysisResult(
            symbols=tuple(symbols),
            import_modules=tuple(sorted(set(import_modules))),
            syntax_valid=True,
        )
        return ToolHandlerResult.success(analysis.to_dict())


class SandboxedCommandRunnerHandler:
    """Runs allowlisted test and linting commands in sandboxed workspace."""

    ALLOWED_COMMANDS = frozenset({"pytest", "ruff", "mypy", "git", "python"})

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path.cwd()

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        command = str(arguments.get("command", ""))
        args = arguments.get("args", [])
        if not isinstance(args, list):
            return ToolHandlerResult.failure(
                ErrorInfo("invalid_args", "args must be a list of strings", "ValidationError")
            )

        if command not in self.ALLOWED_COMMANDS:
            allowed = sorted(self.ALLOWED_COMMANDS)
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "command_denied",
                    f"Command not allowed: {command}. Allowed: {allowed}",
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

        venv_bin = self.workspace_root / ".venv" / "Scripts"
        exe = venv_bin / f"{command}.exe"
        cmd_path = str(exe) if exe.is_file() else command

        full_cmd = [cmd_path] + [str(a) for a in args]
        start_time = time.monotonic()
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
            duration_ms = int((time.monotonic() - start_time) * 1000)
            res = TestRunResult(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                passed=completed.returncode == 0,
                duration_ms=duration_ms,
            )
            return ToolHandlerResult.success(res.to_dict())
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return ToolHandlerResult.failure(
                ErrorInfo("timeout", f"Command timed out after {timeout}s", "TimeoutError")
            )
        except Exception as err:
            return ToolHandlerResult.failure(
                ErrorInfo("execution_failed", f"Execution failed: {err}", "ExecutionError")
            )


__all__ = [
    "ASTAnalysisHandler",
    "CodingProjectDiscoveryHandler",
    "SandboxedCommandRunnerHandler",
]
