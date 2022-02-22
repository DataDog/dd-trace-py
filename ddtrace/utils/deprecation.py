from ..internal.utils.deprecation import get_service_legacy  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.deprecation",
    removal_version="1.0.0",
)
