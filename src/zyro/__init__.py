"""ZYRO foundation package.

Business capabilities are intentionally deferred to later implementation phases.
"""

from zyro.runtime.bootstrap import RuntimeContext, initialize_runtime

__all__ = ["RuntimeContext", "initialize_runtime"]
__version__ = "0.1.0"
