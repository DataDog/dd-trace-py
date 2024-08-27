from ddtrace import tracer # init the global tracer in this module 
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.pin import Pin # move pin to ddtrace._trace


# TODO: Move `ddtrace.Pin`, `ddtrace.Tracer`, `ddtrace.Span`, and `ddtrace.tracer` to this module
__all__ = [
    "Context",
    "Pin",
    "Tracer",
    "tracer",
    "Span",
]
