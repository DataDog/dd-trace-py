from ddtrace.contrib.internal.trace_utils_async import *  # noqa: F403
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The ddtrace.contrib.internal.trace_utils_async module is deprecated",
    message="Import from ``ddtrace.contrib.internal.trace_utils`` instead.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
