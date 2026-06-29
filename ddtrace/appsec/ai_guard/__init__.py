"""Deprecated location for the AI Guard public SDK.

AI Guard has moved to the top-level ``ddtrace.aiguard`` package. This module
re-exports the public symbols from their new home and emits a
``DDTraceDeprecationWarning`` on first access. It will be removed in 5.0.0.
"""

import typing

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


# AIDEV-NOTE: lazy re-export shim. Accessing any public symbol here warns once
# (cached back into globals()) and forwards to its new home. Most symbols moved
# to ddtrace.aiguard; the Strands classes moved to
# ddtrace.aiguard.integrations.strands (they are deliberately not re-exported
# from the top-level package — see that package's __init__). Keep this in sync
# with ddtrace.aiguard.__all__. Remove the whole package in 5.0.0.
_PUBLIC = frozenset(
    {
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
    }
)

# Strands classes live in the integrations submodule, not the top-level package.
# Importing the submodule lazily (only on attribute access) keeps the strands
# integration out of ``import ddtrace.appsec.ai_guard``'s import graph.
_STRANDS = frozenset({"AIGuardStrandsPlugin", "AIGuardStrandsHookProvider"})


def __getattr__(name: str) -> typing.Any:
    if name in _PUBLIC or name in _STRANDS:
        deprecate(  # type: ignore[no-untyped-call]
            prefix="ddtrace.appsec.ai_guard is deprecated",
            message="Import from ddtrace.aiguard instead.",
            removal_version="5.0.0",
            category=DDTraceDeprecationWarning,
        )
        if name in _STRANDS:
            from ddtrace.aiguard.integrations import strands

            attr = getattr(strands, name)
        else:
            import ddtrace.aiguard as new_pkg

            attr = getattr(new_pkg, name)

        globals()[name] = attr  # cache so we warn at most once per symbol
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Computed (not a literal) so the lazily-resolved names don't trip ruff's F822.
__all__ = sorted(_PUBLIC | _STRANDS)
