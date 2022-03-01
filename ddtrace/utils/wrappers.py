from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.wrappers import F  # noqa
from ..internal.utils.wrappers import iswrapped  # noqa
from ..internal.utils.wrappers import safe_patch  # noqa
from ..internal.utils.wrappers import unwrap  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.wrappers",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
