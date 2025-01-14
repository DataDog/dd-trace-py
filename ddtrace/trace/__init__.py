from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer


# a global tracer instance with integration settings
tracer = Tracer()

__all__ = [
    "Context",
    "Tracer",
    "Span",
    "tracer",
]
