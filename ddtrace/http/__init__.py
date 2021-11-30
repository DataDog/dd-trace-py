from ddtrace.internal.utils.deprecation import deprecation

from .headers import store_request_headers
from .headers import store_response_headers


__all__ = [
    "store_request_headers",
    "store_response_headers",
]

deprecation(
    name="ddtrace.http",
    message="The http module has been merged into ddtrace.contrib.trace_utils",
    version="1.0.0",
)
