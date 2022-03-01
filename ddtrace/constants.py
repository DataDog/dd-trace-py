from ddtrace.internal.compat import ensure_pep562
from ddtrace.internal.utils.deprecation import DDTraceDeprecationWarning
from ddtrace.vendor import debtcollector


SAMPLE_RATE_METRIC_KEY = "_sample_rate"
SAMPLING_PRIORITY_KEY = "_sampling_priority_v1"
ANALYTICS_SAMPLE_RATE_KEY = "_dd1.sr.eausr"
SAMPLING_AGENT_DECISION = "_dd.agent_psr"
SAMPLING_RULE_DECISION = "_dd.rule_psr"
SAMPLING_LIMIT_DECISION = "_dd.limit_psr"
ORIGIN_KEY = "_dd.origin"
HOSTNAME_KEY = "_dd.hostname"
ENV_KEY = "env"
VERSION_KEY = "version"
SERVICE_KEY = "service.name"
SERVICE_VERSION_KEY = "service.version"
SPAN_KIND = "span.kind"
SPAN_MEASURED_KEY = "_dd.measured"
KEEP_SPANS_RATE_KEY = "_dd.tracer_kr"


MANUAL_DROP_KEY = "manual.drop"
MANUAL_KEEP_KEY = "manual.keep"


ERROR_MSG = "error.msg"  # a string representing the error message
ERROR_TYPE = "error.type"  # a string representing the type of the error
ERROR_STACK = "error.stack"  # a human readable version of the stack.

PID = "system.pid"

# Use this to explicitly inform the backend that a trace should be rejected and not stored.
USER_REJECT = -1
# Used by the builtin sampler to inform the backend that a trace should be rejected and not stored.
AUTO_REJECT = 0
# Used by the builtin sampler to inform the backend that a trace should be kept and stored.
AUTO_KEEP = 1
# Use this to explicitly inform the backend that a trace should be kept and stored.
USER_KEEP = 2


_DEPRECATED = {
    # TODO: Removal of this attribute does not require the addition of a
    # constant as it has only a single use for passing trace filters in the
    # settings to the tracer.
    "FILTERS_KEY": "FILTERS",
    # TODO: Moved to other modules but cannot reference here due to circular imports
    # ddtrace.contrib.logging.patch._LOG_SPAN_KEY
    # ddtrace.span._NUMERIC_TAGS
    "NUMERIC_TAGS": (ANALYTICS_SAMPLE_RATE_KEY,),
    "LOG_SPAN_KEY": "__datadog_log_span",
}


def __getattr__(name):
    if name in _DEPRECATED:
        debtcollector.deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            category=DDTraceDeprecationWarning,
            removal_version="1.0.0",
        )
        return _DEPRECATED[name]

    if name in globals():
        return globals()[name]

    raise AttributeError("%s has no attribute %s", __name__, name)


ensure_pep562(__name__)
