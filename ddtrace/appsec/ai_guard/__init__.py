"""
AI Guard public SDK
"""

import typing

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


# AIDEV-NOTE: AIGuardStrandsPlugin / AIGuardStrandsHookProvider are exposed via a
# module-level ``__getattr__`` so the strands integration loads on first attribute
# access — not at ``from ddtrace.appsec.ai_guard import AIGuardAbortError`` time.
# Eager loading captured ``BeforeModelCallEvent`` / ``AfterModelCallEvent`` /
# ``BeforeToolCallEvent`` / ``AfterToolCallEvent`` *before* the user's
# ``from strands import Agent`` re-instantiated those dataclasses under
# ``ddtrace.auto``'s import-time instrumentation. The hook registry is keyed by
# class identity, so the plugin's ``@hook`` callbacks ended up registered against
# stale class objects while the agent dispatched with the new ones — net effect:
# no Strands callback ever fired. The actual resolution lives in
# ``ddtrace.appsec.ai_guard.integrations``; this module only re-exports it.
# Regression test: tests/appsec/ai_guard/strands_hooks/test_strands.py::TestLazyImport.


def __getattr__(name: str) -> typing.Any:
    # AIDEV-NOTE: cache by writing back into globals() so subsequent attribute
    # accesses skip ``__getattr__`` entirely (Python only calls module-level
    # __getattr__ when normal name lookup fails). We can't replace this with a
    # normal ``from .integrations import ...`` because that would import the
    # integrations package — and trigger the resolvers — eagerly, defeating
    # the lazy contract.
    if name == "AIGuardStrandsPlugin":
        from .integrations import resolve_strands_plugin

        cls = resolve_strands_plugin()
        globals()[name] = cls
        return cls
    if name == "AIGuardStrandsHookProvider":
        from .integrations import resolve_strands_hook_provider

        cls = resolve_strands_hook_provider()
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "new_ai_guard_client",
    "AIGuardClient",
    "AIGuardClientError",
    "AIGuardAbortError",
    "AIGuardStrandsPlugin",  # noqa: F822 — resolved lazily by module-level __getattr__
    "AIGuardStrandsHookProvider",  # noqa: F822 — resolved lazily by module-level __getattr__
    "ContentPart",
    "Evaluation",
    "Function",
    "ImageURL",
    "Message",
    "Options",
    "ToolCall",
]