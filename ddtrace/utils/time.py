from ..internal.utils.deprecation import deprecation
from ..internal.utils.time import StopWatch  # noqa


deprecation(
    name="ddtrace.utils.time",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
