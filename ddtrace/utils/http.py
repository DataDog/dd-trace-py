from ..internal.utils.deprecation import deprecation
from ..internal.utils.http import normalize_header_name  # noqa
from ..internal.utils.http import strip_query_string  # noqa


deprecation(
    name="ddtrace.utils.http",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
