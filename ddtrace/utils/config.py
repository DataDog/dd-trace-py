from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.config import get_application_name  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.config",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
