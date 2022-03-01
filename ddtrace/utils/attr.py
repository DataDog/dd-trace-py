from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.attr import T  # noqa
from ..internal.utils.attr import from_env  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.attr",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
