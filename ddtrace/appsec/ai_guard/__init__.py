"""
AI Guard public SDK
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
import typing

from ddtrace.internal.logger import get_logger

from ._api_client import AIGuardAbortError
from ._api_client import AIGuardClient
from ._api_client import AIGuardClientError
from ._api_client import ContentPart
from ._api_client import Evaluation
from ._api_client import Function
from ._api_client import ImageURL
from ._api_client import Message
from ._api_client import Options
from ._api_client import ToolCall
from ._api_client import new_ai_guard_client


log = get_logger(__name__)

try:
    version("strands-agents")
    _HAS_STRANDS = True
except PackageNotFoundError:
    _HAS_STRANDS = False


# AIDEV-NOTE: AIGuardStrandsPlugin / AIGuardStrandsHookProvider are exposed via a
# module-level ``__getattr__`` so the strands integration loads on first attribute
# access — not at ``from ddtrace.appsec.ai_guard import AIGuardAbortError`` time.
# Eager loading captured ``BeforeModelCallEvent`` / ``AfterModelCallEvent`` /
# ``BeforeToolCallEvent`` / ``AfterToolCallEvent`` *before* the user's
# ``from strands import Agent`` re-instantiated those dataclasses under
# ``ddtrace.auto``'s import-time instrumentation. The hook registry is keyed by
# class identity, so the plugin's ``@hook`` callbacks ended up registered against
# stale class objects while the agent dispatched with the new ones — net effect:
# no Strands callback ever fired.
# Regression test: tests/appsec/ai_guard/strands_hooks/test_strands.py::TestLazyImport.


def _build_stub_plugin() -> type:
    class AIGuardStrandsPlugin:
        """Stub when strands-agents is not installed or its Plugin API is unavailable."""

        def __init__(self, *args: typing.Any, **kwargs: typing.Any):
            log.warning(
                "AIGuardStrandsPlugin could not be loaded. "
                "Please install strands-agents>=1.29.0: pip install 'strands-agents>=1.29.0'"
            )

    return AIGuardStrandsPlugin


def _build_stub_hook_provider() -> type:
    class AIGuardStrandsHookProvider:
        """Stub when strands-agents is not installed."""

        def __init__(self, *args: typing.Any, **kwargs: typing.Any):
            log.warning(
                "AIGuardStrandsHookProvider could not be loaded. "
                "Please install strands-agents: pip install strands-agents"
            )

        def register_hooks(self, registry: typing.Any, **kwargs: typing.Any) -> None:
            pass

    return AIGuardStrandsHookProvider


def _resolve_strands_plugin() -> type:
    if _HAS_STRANDS:
        try:
            from .integrations.strands import AIGuardStrandsPlugin

            return AIGuardStrandsPlugin
        except ImportError:
            log.debug("Failed to import AIGuardStrandsPlugin", exc_info=True)
    return _build_stub_plugin()


def _resolve_strands_hook_provider() -> type:
    if _HAS_STRANDS:
        try:
            from .integrations.strands import AIGuardStrandsHookProvider

            return AIGuardStrandsHookProvider
        except ImportError:
            log.debug("Failed to import AIGuardStrandsHookProvider", exc_info=True)
    return _build_stub_hook_provider()


def __getattr__(name: str) -> typing.Any:
    # AIDEV-NOTE: cache by writing back into globals() so subsequent attribute
    # accesses skip ``__getattr__`` entirely (Python only calls module-level
    # __getattr__ when normal name lookup fails).
    if name == "AIGuardStrandsPlugin":
        cls = _resolve_strands_plugin()
        globals()[name] = cls
        return cls
    if name == "AIGuardStrandsHookProvider":
        cls = _resolve_strands_hook_provider()
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "new_ai_guard_client",
    "AIGuardClient",
    "AIGuardClientError",
    "AIGuardAbortError",
    "AIGuardStrandsPlugin",  # noqa: F822 — resolved lazily by module-level __getattr__
    "AIGuardStrandsHookProvider",  # noqa: F822 — resolved lazily by module-level __getattr__
    "ContentPart",
    "Evaluation",
    "Function",
    "ImageURL",
    "Message",
    "Options",
    "ToolCall",
]
