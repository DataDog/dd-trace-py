from ddtrace._trace.context import Context  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The context interface is deprecated.",
    message="The trace context is an internal interface and will no longer be supported.",
    category=DDTraceDeprecationWarning,
)
