from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils import ArgumentError  # noqa
from ..internal.utils import get_argument_value  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.__init__",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
