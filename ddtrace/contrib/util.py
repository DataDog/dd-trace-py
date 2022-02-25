# [Backward compatibility]: keep importing modules functions

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.importlib import func_name
from ..internal.utils.importlib import module_name
from ..internal.utils.importlib import require_modules
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.contrib.util",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)

__all__ = [
    "require_modules",
    "func_name",
    "module_name",
]
