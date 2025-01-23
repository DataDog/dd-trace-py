from ddtrace.trace.internal.context import Context
from ddtrace.trace.internal.filters import TraceFilter
from ddtrace.trace.internal.pin import Pin
from ddtrace.trace.internal.span import Span
from ddtrace.trace.internal.tracer import Tracer


# a global tracer instance with integration settings
tracer = Tracer()

__all__ = [
    "Context",
    "Pin",
    "TraceFilter",
    "Tracer",
    "Span",
    "tracer",
]
