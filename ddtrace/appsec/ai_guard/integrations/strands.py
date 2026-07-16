"""Deprecated location for the AI Guard Strands Agents integration.

Moved to ``ddtrace.aiguard.integrations.strands``. This shim re-exports the
public symbols from their new home and emits a ``DDTraceDeprecationWarning``
on first access. It will be removed in 5.0.0.
"""

import typing

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


# Keep in sync with the public surface of ddtrace.aiguard.integrations.strands.
_PUBLIC = frozenset(
    {
        "AIGuardStrandsIntegration",
        "AIGuardStrandsPlugin",
        "AIGuardStrandsHookProvider",
    }
)


def __getattr__(name: str) -> typing.Any:
    if name in _PUBLIC:
        deprecate(  # type: ignore[no-untyped-call]
            prefix="ddtrace.appsec.ai_guard.integrations.strands is deprecated",
            message="Import from ddtrace.aiguard.integrations.strands instead.",
            removal_version="5.0.0",
            category=DDTraceDeprecationWarning,
        )
        from ddtrace.aiguard.integrations import strands

        attr = getattr(strands, name)
        globals()[name] = attr  # cache so we warn at most once per symbol
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Computed (not a literal) so the lazily-resolved names don't trip ruff's F822.
__all__ = sorted(_PUBLIC)
