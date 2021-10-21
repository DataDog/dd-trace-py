from ..internal.utils.__init__ import ArgumentError  # noqa
from ..internal.utils.__init__ import get_argument_value  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.__init__",
    message="Use `ddtrace.internal.utils.__init__` module instead",
    version="1.0.0",
)
