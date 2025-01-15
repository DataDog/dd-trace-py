from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Use ``ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
