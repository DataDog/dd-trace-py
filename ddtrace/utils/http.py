from ..internal.utils.deprecation import deprecation
from ..internal.utils.http import normalize_header_name  # noqa
from ..internal.utils.http import strip_query_string  # noqa


deprecation(
    name="ddtrace.utils.http",
    message="Use `ddtrace.internal.utils.http` module instead",
    version="1.0.0",
)
