from ..internal.utils.deprecation import deprecation
from ..internal.utils.time import StopWatch  # noqa


deprecation(
    name="ddtrace.utils.time",
    message="Use `ddtrace.internal.utils.time` module instead",
    version="1.0.0",
)
