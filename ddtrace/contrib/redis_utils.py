from ddtrace.contrib.internal.redis_utils import *  # noqa: F403
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The ddtrace.contrib.redis_utils module is deprecated",
    message="Import from ``ddtrace.contrib.trace_utils`` instead.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
