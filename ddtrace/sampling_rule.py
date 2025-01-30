from ddtrace._trace.sampling_rule import *  # noqa: F403
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    "The ddtrace.sample_rule module is deprecated and will be removed.",
    message="Use DD_TRACE_SAMPLING_RULES to set sampling rules.",
    category=DDTraceDeprecationWarning,
)
