from typing import Any

from ddtrace._trace.context import Context
from ddtrace._trace.filters import TraceFilter
from ddtrace._trace.pin import Pin as _Pin
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.internal import core
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


# a global tracer instance with integration settings
tracer = Tracer()
core.tracer = tracer  # type: ignore


def __getattr__(name: str) -> Any:
    if name == "Pin":
        deprecate(
            prefix="ddtrace.trace.Pin is deprecated",
            message="Please use environment variables for configuration instead",
            category=DDTraceDeprecationWarning,
            removal_version="4.0.0",
        )
        return _Pin
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "BaseContextProvider",
    "Context",
    "Pin",
    "TraceFilter",
    "Tracer",
    "Span",
    "tracer",
]
