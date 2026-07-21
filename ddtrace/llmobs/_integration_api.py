"""Minimal LLMObs API used by integrations during their import-time setup.

Integrations can be imported while the full LLMObs service is initializing. Keep
this module independent from ``_llmobs`` so integration setup does not recursively
import the service through ``BaseLLMIntegration``.
"""

from typing import Any
from typing import Optional


_llmobs_service: Optional[Any] = None


def register_llmobs_service(service: Any) -> None:
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
