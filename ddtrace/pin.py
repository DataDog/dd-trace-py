from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.trace.internal.pin import *  # noqa: F403
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The ddtrace.trace.Pin module is deprecated and will be removed.",
    message="Import ``Pin`` from the ddtrace.trace package.",
    category=DDTraceDeprecationWarning,
)
