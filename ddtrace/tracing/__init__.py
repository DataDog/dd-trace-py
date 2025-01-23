from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.trace.internal import trace_handlers  # noqa: F401
from ddtrace.trace.internal._span_link import SpanLink  # noqa: F401
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The tracing module is deprecated and will be moved.",
    message="A new interface will be provided by the ddtrace.trace sub-package.",
    category=DDTraceDeprecationWarning,
)
