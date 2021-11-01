from ..internal.utils.__init__ import ArgumentError  # noqa
from ..internal.utils.__init__ import get_argument_value  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.__init__",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
