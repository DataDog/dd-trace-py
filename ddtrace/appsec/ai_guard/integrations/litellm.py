"""Deprecated location for the AI Guard LiteLLM integration.

Moved to ``ddtrace.aiguard.integrations.litellm``. This shim re-exports the
public symbols from their new home and emits a ``DDTraceDeprecationWarning``
on first access. It will be removed in 5.0.0.
"""

import typing

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


# Keep in sync with the public surface of ddtrace.aiguard.integrations.litellm.
_PUBLIC = frozenset(
    {
        "GUARDRAIL_NAME",
        "DatadogAIGuardGuardrail",
        "DatadogAIGuardGuardrailException",
    }
)


def __getattr__(name: str) -> typing.Any:
    if name in _PUBLIC:
        deprecate(  # type: ignore[no-untyped-call]
            prefix="ddtrace.appsec.ai_guard.integrations.litellm is deprecated",
            message="Import from ddtrace.aiguard.integrations.litellm instead.",
            removal_version="5.0.0",
            category=DDTraceDeprecationWarning,
        )
        from ddtrace.aiguard.integrations import litellm

        attr = getattr(litellm, name)
        globals()[name] = attr  # cache so we warn at most once per symbol
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Computed (not a literal) so the lazily-resolved names don't trip ruff's F822.
__all__ = sorted(_PUBLIC)
