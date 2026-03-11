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

# HookProvider and Plugin are imported independently so that a
# missing Plugin API (strands-agents < 1.29.0) does not break the legacy
# HookProvider import.
_HAS_STRANDS_HOOK_PROVIDER = False
_HAS_STRANDS_PLUGIN = False

if _HAS_STRANDS:
    try:
        from .integrations.strands import AIGuardStrandsHookProvider

        _HAS_STRANDS_HOOK_PROVIDER = True
    except ImportError:
        log.debug("Failed to import AIGuardStrandsHookProvider", exc_info=True)

    try:
        from .integrations.strands import AIGuardStrandsPlugin

        _HAS_STRANDS_PLUGIN = True
    except ImportError:
        log.debug("Failed to import AIGuardStrandsPlugin", exc_info=True)

if not _HAS_STRANDS_PLUGIN:

    class AIGuardStrandsPlugin:  # type: ignore[no-redef]
        """Stub AIGuardStrandsPlugin when strands-agents is not installed.

        Logs a warning when instantiated, informing users to install
        strands-agents >= 1.29.0.
        """

        def __init__(self, *args: typing.Any, **kwargs: typing.Any):
            log.warning(
                "AIGuardStrandsPlugin could not be loaded. "
                "Please install strands-agents>=1.29.0: pip install 'strands-agents>=1.29.0'"
            )


if not _HAS_STRANDS_HOOK_PROVIDER:

    class AIGuardStrandsHookProvider:  # type: ignore[no-redef]
        """Stub AIGuardStrandsHookProvider when strands-agents is not installed.

        Logs a warning when instantiated, informing users to install the strands-agents package.
        """

        def __init__(self, *args: typing.Any, **kwargs: typing.Any):
            log.warning(
                "AIGuardStrandsHookProvider could not be loaded. "
                "Please install strands-agents: pip install strands-agents"
            )

        def register_hooks(self, registry: typing.Any, **kwargs: typing.Any) -> None:
            pass


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
