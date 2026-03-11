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

if _HAS_STRANDS:
    try:
        from .integrations.strands import AIGuardStrandsHookProvider
        from .integrations.strands import AIGuardStrandsPlugin
    except ImportError:
        log.debug("Failed to import Strands integration", exc_info=True)
        _HAS_STRANDS = False

if not _HAS_STRANDS:

    class AIGuardStrandsPlugin:  # type: ignore[no-redef]
        """Stub AIGuardStrandsPlugin when strands-agents is not installed.

        Logs a warning when instantiated, informing users to install the strands-agents package.
        """

        def __init__(self, *args: typing.Any, **kwargs: typing.Any):
            log.warning(
                "AIGuardStrandsPlugin could not be loaded. Please install strands-agents: pip install strands-agents"
            )

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
