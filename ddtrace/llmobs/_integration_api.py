"""Minimal LLMObs API shared by integrations and evaluators.

Integrations can be imported while the full LLMObs service is initializing. Keep
this module independent from _llmobs, including for type checking, so consumers
can access the registered service without depending on its implementation.
"""

from __future__ import annotations

from typing import Any
from typing import Optional
from typing import Protocol


class _LLMObsService(Protocol):
    """Service operations needed by import-time consumers."""

    @property
    def enabled(self) -> bool: ...

    def annotate(self, *args: Any, **kwargs: Any) -> None: ...


# NOTE: The registered object is the LLMObs class, not an instance. Its class
# methods satisfy the protocol without a reverse dependency on _llmobs.
_llmobs_service: Optional[_LLMObsService] = None


def register_llmobs_service(service: _LLMObsService) -> None:
    """Register the concrete LLMObs service after it has finished importing."""
    global _llmobs_service
    _llmobs_service = service


def get_llmobs_service() -> Optional[_LLMObsService]:
    """Return the registered service class, or None before registration."""
    return _llmobs_service


def is_enabled() -> bool:
    """Return whether the registered LLMObs service is enabled."""
    return bool(_llmobs_service and _llmobs_service.enabled)


def annotate(*args: Any, **kwargs: Any) -> None:
    """Delegate annotations to the registered LLMObs service, when available."""
    if _llmobs_service is not None:
        _llmobs_service.annotate(*args, **kwargs)
