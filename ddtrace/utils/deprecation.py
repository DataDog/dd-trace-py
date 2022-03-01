from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.deprecation import get_service_legacy  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.deprecation",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
