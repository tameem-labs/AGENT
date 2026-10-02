"""Expected bounded-tool contract errors."""

from zyro.core.errors import ZyroError


class InvalidToolDefinitionError(ZyroError, ValueError):
    """Raised when tool metadata violates its contract."""


class DuplicateToolError(ZyroError, ValueError):
    """Raised when a tool identity is registered more than once."""


class MissingToolError(ZyroError, LookupError):
    """Raised when a tool identity is not registered."""
