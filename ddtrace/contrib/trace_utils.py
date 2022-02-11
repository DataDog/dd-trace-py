# [Backward compatibility]: keep importing modules functions
from ..internal.utils.deprecation import deprecation
from ..tracing._utils import NORMALIZE_PATTERN
from ..tracing._utils import REQUEST
from ..tracing._utils import RESPONSE
from ..tracing._utils import _normalize_tag_name
from ..tracing._utils import _normalized_header_name
from ..tracing._utils import _store_headers
from ..tracing._utils import _store_request_headers
from ..tracing._utils import _store_response_headers
from ..tracing._utils import set_flattened_tags
from ..tracing._utils import set_http_meta
from ..tracing.utils import activate_distributed_headers
from ..tracing.utils import distributed_tracing_enabled
from ..tracing.utils import ext_service
from ..tracing.utils import int_service
from ..tracing.utils import with_traced_module


deprecation(
    name="ddtrace.contrib.tracing_utils",
    message="`ddtrace.contrib.tracing_utils` module will be removed in 2.0.0",
    version="1.0.0",
)

__all__ = [
    "int_service",
    "ext_service",
    "with_traced_module",
    "distributed_tracing_enabled",
    "activate_distributed_headers",
    "set_flattened_tags",
    "_normalized_header_name",
    "_normalize_tag_name",
    "set_http_meta",
    "_store_headers",
    "_store_request_headers",
    "_store_response_headers",
    "NORMALIZE_PATTERN",
    "REQUEST",
    "RESPONSE",
]
