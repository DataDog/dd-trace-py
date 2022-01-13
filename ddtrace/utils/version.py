from ..internal.utils.deprecation import deprecation
from ..internal.utils.version import parse_version  # noqa


deprecation(
    name="ddtrace.utils.version",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
