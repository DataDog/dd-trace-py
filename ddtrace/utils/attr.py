from ..internal.utils.attr import T  # noqa
from ..internal.utils.attr import from_env  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.attr",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
