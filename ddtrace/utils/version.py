from ..internal.utils.deprecation import deprecation
from ..internal.utils.version import parse_version  # noqa


deprecation(
    name="ddtrace.utils.version",
    message="Use `ddtrace.internal.utils.version` module instead",
    version="1.0.0",
)
