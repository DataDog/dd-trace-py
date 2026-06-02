"""
Lazy loaders for AI Guard third-party integrations.

The ``ddtrace.appsec.ai_guard`` package re-exports the Strands classes via a
module-level ``__getattr__`` that delegates to the resolvers in this module.
The actual ``.strands`` submodule is only imported on first attribute access,
not at ``ddtrace.appsec.ai_guard`` import time. See the AIDEV-NOTE in the
parent package's ``__init__.py`` for the regression this protects against.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
import typing

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

try:
    version("strands-agents")
    _HAS_STRANDS = True
except PackageNotFoundError:
    _HAS_STRANDS = False


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


def resolve_strands_plugin() -> type:
    if _HAS_STRANDS:
        try:
            from .strands import AIGuardStrandsPlugin

            return AIGuardStrandsPlugin
        except ImportError:
            log.debug("Failed to import AIGuardStrandsPlugin", exc_info=True)
    return _build_stub_plugin()


def resolve_strands_hook_provider() -> type:
    if _HAS_STRANDS:
        try:
            from .strands import AIGuardStrandsHookProvider

            return AIGuardStrandsHookProvider
        except ImportError:
            log.debug("Failed to import AIGuardStrandsHookProvider", exc_info=True)
    return _build_stub_hook_provider()
