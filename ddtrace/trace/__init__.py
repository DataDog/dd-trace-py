from ddtrace._trace.context import Context
from ddtrace._trace.filters import TraceFilter
from ddtrace._trace.pin import Pin
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.internal import core


# a global tracer instance with integration settings
tracer = Tracer()
core.tracer = tracer  # type: ignore


__all__ = [
    "BaseContextProvider",
    "Context",
    "Pin",
    "TraceFilter",
    "Tracer",
    "Span",
    "tracer",
]
