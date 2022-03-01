from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.http import normalize_header_name  # noqa
from ..internal.utils.http import strip_query_string  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.http",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
