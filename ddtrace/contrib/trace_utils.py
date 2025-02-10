from ddtrace.contrib.internal.redis_utils import MULTI_KEY_COMMANDS  # noqa: F401
from ddtrace.contrib.internal.redis_utils import ROW_RETURNING_COMMANDS  # noqa: F401
from ddtrace.contrib.internal.redis_utils import SINGLE_KEY_COMMANDS  # noqa: F401
from ddtrace.contrib.internal.redis_utils import determine_row_count  # noqa: F401
from ddtrace.contrib.internal.trace_utils import IP_PATTERNS  # noqa: F401
from ddtrace.contrib.internal.trace_utils import NORMALIZE_PATTERN  # noqa: F401
from ddtrace.contrib.internal.trace_utils import REQUEST  # noqa: F401
from ddtrace.contrib.internal.trace_utils import RESPONSE  # noqa: F401
from ddtrace.contrib.internal.trace_utils import USER_AGENT_PATTERNS  # noqa: F401
from ddtrace.contrib.internal.trace_utils import activate_distributed_headers  # noqa: F401
from ddtrace.contrib.internal.trace_utils import distributed_tracing_enabled  # noqa: F401
from ddtrace.contrib.internal.trace_utils import ext_service  # noqa: F401
from ddtrace.contrib.internal.trace_utils import extract_netloc_and_query_info_from_url  # noqa: F401
from ddtrace.contrib.internal.trace_utils import int_service  # noqa: F401
from ddtrace.contrib.internal.trace_utils import iswrapped  # noqa: F401
from ddtrace.contrib.internal.trace_utils import set_flattened_tags  # noqa: F401
from ddtrace.contrib.internal.trace_utils import set_http_meta  # noqa: F401
from ddtrace.contrib.internal.trace_utils import set_user  # noqa: F401
from ddtrace.contrib.internal.trace_utils import unwrap  # noqa: F401
from ddtrace.contrib.internal.trace_utils import with_traced_module  # noqa: F401
from ddtrace.contrib.internal.trace_utils import wrap  # noqa: F401
from ddtrace.contrib.internal.trace_utils_async import with_traced_module as with_traced_module_async  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


# TODO: Add trace utils that should not be public to the list below
_DEPRECATED_MODULE_ATTRIBUTES = []


def __getattr__(name):
    if name in _DEPRECATED_MODULE_ATTRIBUTES:
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]

    raise AttributeError("%s has no attribute %s", __name__, name)
