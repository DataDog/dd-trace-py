from ..internal.utils.config import get_application_name  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.config",
    message="Use `ddtrace.internal.utils.config` module instead",
    version="1.0.0",
)
