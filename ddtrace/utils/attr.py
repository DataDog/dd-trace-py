from ..internal.utils.attr import T  # noqa
from ..internal.utils.attr import from_env  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.attr",
    message="Use `ddtrace.internal.utils.attr` module instead",
    version="1.0.0",
)
