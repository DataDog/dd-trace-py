from ddtrace._trace.pin import Pin  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The ddtrace.pin module is deprecated and will be removed.",
    message="A new interface will be provided by the trace sub-package.",
    category=DDTraceDeprecationWarning,
)
