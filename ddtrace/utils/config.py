from ..internal.utils.config import get_application_name  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.config",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
