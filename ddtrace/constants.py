import sys

from ddtrace.internal.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.internal.constants import MANUAL_DROP_KEY
from ddtrace.internal.constants import MANUAL_KEEP_KEY
from ddtrace.internal.constants import SPAN_KIND
from ddtrace.internal.constants import SPAN_MEASURED_KEY
from ddtrace.vendor import debtcollector


__all__ = [
    "ANALYTICS_SAMPLE_RATE_KEY",
    "MANUAL_DROP_KEY",
    "MANUAL_KEEP_KEY",
    "SPAN_KIND",
    "SPAN_MEASURED_KEY",
]


if sys.version_info >= (3, 7, 0):
    import ddtrace.internal.constants

    def __getattr__(name):
        try:
            constant = getattr(ddtrace.internal.constants, name)
        except AttributeError:
            raise AttributeError("module %s has no attribute %s" % (__name__, name))
        else:
            debtcollector.deprecate(("constant %s has been deprecated and will be removed in v1.0" % name))
            return constant


else:
    from ddtrace.internal.constants import ENV_KEY
    from ddtrace.internal.constants import FILTERS_KEY
    from ddtrace.internal.constants import HOSTNAME_KEY
    from ddtrace.internal.constants import KEEP_SPANS_RATE_KEY
    from ddtrace.internal.constants import LOG_SPAN_KEY
    from ddtrace.internal.constants import NUMERIC_TAGS
    from ddtrace.internal.constants import ORIGIN_KEY
    from ddtrace.internal.constants import SAMPLE_RATE_METRIC_KEY
    from ddtrace.internal.constants import SAMPLING_AGENT_DECISION
    from ddtrace.internal.constants import SAMPLING_LIMIT_DECISION
    from ddtrace.internal.constants import SAMPLING_PRIORITY_KEY
    from ddtrace.internal.constants import SAMPLING_RULE_DECISION
    from ddtrace.internal.constants import SERVICE_KEY
    from ddtrace.internal.constants import SERVICE_VERSION_KEY
    from ddtrace.internal.constants import VERSION_KEY

    __all__ += [
        "ENV_KEY",
        "FILTERS_KEY",
        "HOSTNAME_KEY",
        "KEEP_SPANS_RATE_KEY",
        "LOG_SPAN_KEY",
        "NUMERIC_TAGS",
        "ORIGIN_KEY",
        "SAMPLE_RATE_METRIC_KEY",
        "SAMPLING_AGENT_DECISION",
        "SAMPLING_LIMIT_DECISION",
        "SAMPLING_PRIORITY_KEY",
        "SAMPLING_RULE_DECISION",
        "SERVICE_KEY",
        "SERVICE_VERSION_KEY",
        "VERSION_KEY",
    ]
