"""Expected model-system contract errors."""

from zyro.core.errors import ZyroError


class InvalidModelDefinitionError(ZyroError, ValueError):
    """Raised when model metadata violates its contract."""


class InvalidModelRequirementsError(ZyroError, ValueError):
    """Raised when provider-independent requirements are invalid."""


class DuplicateModelError(ZyroError, ValueError):
    """Raised when a model identity is registered more than once."""


class MissingModelError(ZyroError, LookupError):
    """Raised when a model identity is not registered."""


class InvalidProviderError(ZyroError, ValueError):
    """Raised when provider registration metadata is invalid."""


class DuplicateProviderError(ZyroError, ValueError):
    """Raised when a provider identity is registered more than once."""


class MissingProviderError(ZyroError, LookupError):
    """Raised when a provider identity is not registered."""
