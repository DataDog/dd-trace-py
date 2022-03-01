from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.time import StopWatch  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.time",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
