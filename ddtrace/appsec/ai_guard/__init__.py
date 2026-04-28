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


class _AIGuardStrandsMissingStub:
    """Fallback used when strands-agents is not installed."""

    _missing_message = (
        "AIGuardStrandsPlugin could not be loaded. "
        "Please install strands-agents>=1.29.0: pip install 'strands-agents>=1.29.0'"
    )

    def __init__(self, *args: typing.Any, **kwargs: typing.Any):
        log.warning(self._missing_message)

    def register_hooks(self, registry: typing.Any, **kwargs: typing.Any) -> None:
        pass


# AIDEV-NOTE: The Strands integration is loaded LAZILY (via module __getattr__)
# rather than eagerly imported at module load time. Eager import here breaks
# Strands hook dispatch under ddtrace.auto: importing
# ``ddtrace.appsec.ai_guard.integrations.strands`` pulls in ``strands.hooks``
# before ddtrace's ModuleWatchdog has registered for that package, and the
# subsequent user-driven ``from strands import Agent`` re-creates the event
# dataclasses with new class identities. The plugin's @hook callbacks would
# end up registered against the *old* class identities while the agent
# dispatches with the *new* ones, so no callback ever fires. Deferring the
# import to first attribute access guarantees ``strands.hooks`` is already
# fully loaded by the user's own ``import strands`` before our integration
# binds its event-type references.
def __getattr__(name: str) -> typing.Any:
    if name not in ("AIGuardStrandsPlugin", "AIGuardStrandsHookProvider"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    if not _HAS_STRANDS:
        globals()[name] = _AIGuardStrandsMissingStub
        return _AIGuardStrandsMissingStub

    try:
        from .integrations import strands as _strands_integration
    except ImportError:
        log.debug("Failed to import %s", name, exc_info=True)
        globals()[name] = _AIGuardStrandsMissingStub
        return _AIGuardStrandsMissingStub

    obj = getattr(_strands_integration, name, _AIGuardStrandsMissingStub)
    globals()[name] = obj
    return obj


__all__ = [
    "new_ai_guard_client",
    "AIGuardClient",
    "AIGuardClientError",
    "AIGuardAbortError",
    "AIGuardStrandsPlugin",
    "AIGuardStrandsHookProvider",
    "ContentPart",
    "Evaluation",
    "Function",
    "ImageURL",
    "Message",
    "Options",
    "ToolCall",
]
