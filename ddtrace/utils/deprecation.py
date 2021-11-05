from ..internal.utils.deprecation import RemovedInDDTrace10Warning  # noqa
from ..internal.utils.deprecation import deprecated  # noqa
from ..internal.utils.deprecation import deprecation
from ..internal.utils.deprecation import format_message  # noqa
from ..internal.utils.deprecation import get_service_legacy  # noqa
from ..internal.utils.deprecation import warn  # noqa


deprecation(
    name="ddtrace.utils.deprecation",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
