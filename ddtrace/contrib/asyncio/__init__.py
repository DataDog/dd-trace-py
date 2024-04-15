"""
This integration provides context management for tracing the execution flow
of concurrent execution of ``asyncio.Task``.
"""
from ...internal.utils.importlib import require_modules


required_modules = ["asyncio"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from ddtrace._trace.provider import DefaultContextProvider

        context_provider = DefaultContextProvider()

        from .helpers import ensure_future
        from .helpers import run_in_executor
        from .helpers import set_call_context
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch  # noqa: F401

        __all__ = ["context_provider", "set_call_context", "ensure_future", "run_in_executor", "patch", "get_version"]
