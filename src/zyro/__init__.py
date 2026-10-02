"""ZYRO core package with a provider-independent Executive and agent runtime."""

from zyro.core.executive import ExecutiveResult, UserRequest, ZyroExecutive
from zyro.runtime.bootstrap import RuntimeContext, initialize_runtime

__all__ = [
    "ExecutiveResult",
    "RuntimeContext",
    "UserRequest",
    "ZyroExecutive",
    "initialize_runtime",
]
__version__ = "0.2.0"
