"""Contracts for the Coding Department."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zyro.core.data import validate_text


@dataclass(frozen=True, slots=True)
class CodeProject:
    project_path: str
    name: str
    language: str
    has_git: bool

    def __post_init__(self) -> None:
        for name in ("project_path", "name", "language"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class ASTSymbol:
    kind: str
    name: str
    line: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", validate_text(self.kind, "kind"))
        object.__setattr__(self, "name", validate_text(self.name, "name"))
        if self.line < 1:
            raise ValueError("line number must be positive")


@dataclass(frozen=True, slots=True)
class ASTAnalysisResult:
    symbols: tuple[ASTSymbol, ...]
    import_modules: tuple[str, ...]
    syntax_valid: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": [{"kind": s.kind, "name": s.name, "line": s.line} for s in self.symbols],
            "import_modules": list(self.import_modules),
            "syntax_valid": self.syntax_valid,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class TestRunResult:
    exit_code: int
    stdout: str
    stderr: str
    passed: bool
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout[:4000],
            "stderr": self.stderr[:4000],
            "passed": self.passed,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    severity: str
    file: str
    line: int
    message: str
    suggestion: str | None = None

    def __post_init__(self) -> None:
        for name in ("severity", "file", "message"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))


__all__ = [
    "ASTAnalysisResult",
    "ASTSymbol",
    "CodeProject",
    "ReviewFinding",
    "TestRunResult",
]
