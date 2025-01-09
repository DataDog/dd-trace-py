from ddtrace._trace.sampler import *  # noqa: F403
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The ddtrace.sampler module is deprecated and will be removed.",
    message="Use ddtrace configurations to configure sampling rates.",
    category=DDTraceDeprecationWarning,
)
