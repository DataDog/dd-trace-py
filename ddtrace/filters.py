from ddtrace._trace.filters import *  # noqa: F403
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The ddtrace.filters module and the ``FilterRequestsOnUrl`` class is deprecated and will be removed.",
    message="Import ``TraceFilter`` from the ddtrace.trace package.",
    category=DDTraceDeprecationWarning,
)
