from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.attrdict import AttrDict  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.attrdict",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
