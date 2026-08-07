"""
AI Guard public SDK
"""

from typing import TYPE_CHECKING
from typing import Any


# AIDEV-NOTE: Lazy re-exports (PEP 562). Importing an internal submodule (e.g.
# ddtrace.aiguard._constants from ddtrace.internal.settings.aiguard, which is
# loaded during product discovery on every ddtrace-run) must NOT drag in the
# client stack. Eager imports here would load ddtrace.aiguard._api_client (and
# its ddtrace.config/telemetry chain) even when DD_AI_GUARD_ENABLED=false,
# regressing the zero-overhead disabled path. Keep these imports lazy.
if TYPE_CHECKING:
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


__all__ = [
    "new_ai_guard_client",
    "AIGuardClient",
    "AIGuardClientError",
    "AIGuardAbortError",
    "ContentPart",
    "Evaluation",
    "Function",
    "ImageURL",
    "Message",
    "Options",
    "ToolCall",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import _api_client

        return getattr(_api_client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
