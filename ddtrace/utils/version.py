from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.version import parse_version  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.version",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
