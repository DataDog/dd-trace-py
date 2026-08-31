import sys

from ddtrace._trace.context import Context
from ddtrace._trace.filters import TraceFilter
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.internal import core
from ddtrace.internal.runtime import on_runtime_identity_refresh


# a global tracer instance with integration settings
tracer = Tracer()
core.root.set_item("tracer", tracer)
on_runtime_identity_refresh(tracer._refresh_runtime_identity)

if sys.platform == "linux":
    from ddtrace.internal.opentelemetry.thread_context import register_otel_thread_context_listener

    register_otel_thread_context_listener(tracer)


__all__ = [
    "BaseContextProvider",
    "Context",
    "TraceFilter",
    "Tracer",
    "Span",
    "tracer",
]
