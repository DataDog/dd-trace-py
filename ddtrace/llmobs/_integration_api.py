"""Minimal LLMObs API used by integrations during their import-time setup.

Integrations can be imported while the full LLMObs service is initializing. Keep
this module independent from ``_llmobs`` so integration setup does not recursively
import the service through ``BaseLLMIntegration``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Optional


if TYPE_CHECKING:
    from ddtrace.llmobs._llmobs import LLMObs


# The registered service is the ``LLMObs`` class itself, not an instance. The
# annotation is only resolved by type checkers (via ``from __future__ import
# annotations``), so importing ``LLMObs`` here would never run at runtime and the
# module stays free of the ``_llmobs`` dependency it is meant to avoid.
_llmobs_service: Optional[type[LLMObs]] = None


def register_llmobs_service(service: type[LLMObs]) -> None:
    """Register the concrete LLMObs service after it has finished importing."""
    global _llmobs_service
    _llmobs_service = service


def is_enabled() -> bool:
    """Return whether the registered LLMObs service is enabled."""
    return bool(_llmobs_service and _llmobs_service.enabled)


def annotate(*args: Any, **kwargs: Any) -> None:
    """Delegate annotations to the registered LLMObs service, when available."""
    if _llmobs_service is not None:
        _llmobs_service.annotate(*args, **kwargs)
