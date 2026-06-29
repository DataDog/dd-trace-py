"""
AI Guard public SDK
"""

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


# AIDEV-NOTE: the Strands plugin/hook classes are intentionally NOT re-exported
# here — import them from ``ddtrace.aiguard.integrations.strands``. Re-exporting
# would let ``import ddtrace.aiguard`` load strands before the user's
# ``from strands import Agent``, binding the plugin's @hook callbacks to event
# dataclasses that ``ddtrace.auto`` later re-instantiates (so they never fire).
# Regression: tests/aiguard/strands_hooks/test_strands.py::TestLazyImport.
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
