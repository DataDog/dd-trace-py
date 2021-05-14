from ddtrace.internal.compat import ensure_pep562
import ddtrace.internal.constants as _constants
from ddtrace.internal.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.internal.constants import MANUAL_DROP_KEY
from ddtrace.internal.constants import MANUAL_KEEP_KEY
from ddtrace.internal.constants import ORIGIN_KEY
from ddtrace.internal.constants import SAMPLING_PRIORITY_KEY
from ddtrace.internal.constants import SERVICE_KEY
from ddtrace.internal.constants import SERVICE_VERSION_KEY
from ddtrace.internal.constants import SPAN_KIND
from ddtrace.internal.constants import SPAN_MEASURED_KEY
from ddtrace.internal.constants import VERSION_KEY
from ddtrace.vendor import debtcollector


__all__ = [
    "ANALYTICS_SAMPLE_RATE_KEY",
    "MANUAL_DROP_KEY",
    "MANUAL_KEEP_KEY",
    "ORIGIN_KEY",
    "SAMPLING_PRIORITY_KEY",
    "SERVICE_KEY",
    "SERVICE_VERSION_KEY",
    "SPAN_KIND",
    "SPAN_MEASURED_KEY",
    "VERSION_KEY",
]


def __getattr__(name):
    constant = getattr(_constants, name)
    debtcollector.deprecate(("constant %s has been deprecated and will be removed in v1.0" % name))
    return constant


ensure_pep562(__name__)
