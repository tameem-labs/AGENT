"""Structured core errors and explicit contract exceptions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """A safe, structured failure that can cross orchestration boundaries."""

    code: str
    message: str
    error_type: str
    retryable: bool = False


class ZyroError(Exception):
    """Base class for expected ZYRO contract errors."""


class InvalidRequestError(ZyroError, ValueError):
    """Raised when a user request cannot become a task."""


class InvalidTaskError(ZyroError, ValueError):
    """Raised when task data violates the task contract."""


class InvalidTaskTransition(ZyroError, RuntimeError):
    """Raised when a task lifecycle transition is not permitted."""


class InvalidAgentError(ZyroError, ValueError):
    """Raised when agent metadata violates its contract."""


class MissingAgentError(ZyroError, LookupError):
    """Raised when an agent identifier is not registered."""


class InvalidAgentTransition(ZyroError, RuntimeError):
    """Raised when an agent instance transition is not permitted."""
