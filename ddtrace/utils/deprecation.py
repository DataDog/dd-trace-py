from ..internal.utils.deprecation import RemovedInDDTrace10Warning  # noqa
from ..internal.utils.deprecation import deprecated  # noqa
from ..internal.utils.deprecation import format_message  # noqa
from ..internal.utils.deprecation import get_service_legacy  # noqa
from ..internal.utils.deprecation import warn  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.deprecation",
    removal_version="1.0.0",
)
