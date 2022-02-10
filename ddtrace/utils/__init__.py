from ..internal.utils import ArgumentError  # noqa
from ..internal.utils import get_argument_value  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.__init__",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
