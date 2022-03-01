from ddtrace.contrib.trace_utils import NORMALIZE_PATTERN
from ddtrace.contrib.trace_utils import REQUEST
from ddtrace.contrib.trace_utils import RESPONSE
from ddtrace.contrib.trace_utils import _normalize_tag_name
from ddtrace.contrib.trace_utils import _normalized_header_name
from ddtrace.contrib.trace_utils import _store_headers
from ddtrace.contrib.trace_utils import _store_request_headers as store_request_headers
from ddtrace.contrib.trace_utils import _store_response_headers as store_response_headers
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..vendor.debtcollector.removals import removed_module


__all__ = (
    "store_request_headers",
    "store_response_headers",
    "NORMALIZE_PATTERN",
    "REQUEST",
    "RESPONSE",
    "_store_headers",
    "_normalized_header_name",
    "_normalize_tag_name",
)


removed_module(
    module="ddtrace.http.headers",
    replacement="ddtrace.contrib.trace_utils",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
