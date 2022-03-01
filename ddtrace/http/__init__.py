from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..vendor.debtcollector.removals import removed_module
from .headers import store_request_headers
from .headers import store_response_headers


__all__ = [
    "store_request_headers",
    "store_response_headers",
]

removed_module(
    module="ddtrace.http",
    replacement="ddtrace.contrib.trace_utils",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
