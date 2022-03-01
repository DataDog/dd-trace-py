"""
tags for common error attributes
"""

import traceback

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..vendor.debtcollector.removals import remove
from ..vendor.debtcollector.removals import removed_module


__all__ = [ERROR_MSG, ERROR_TYPE, ERROR_STACK]

removed_module(
    module="ddtrace.ext.errors",
    replacement="ddtrace.constants",
    message="ERROR_MSG, ERROR_TYPE, and ERROR_STACK.",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)

# shorthand for ERROR constants to be removed in v1.0-----^
MSG = ERROR_MSG
TYPE = ERROR_TYPE
STACK = ERROR_STACK


@remove(category=DDTraceDeprecationWarning, removal_version="1.0.0")
def get_traceback(tb=None, error=None):
    t = None
    if error:
        t = type(error)
    lines = traceback.format_exception(t, error, tb, limit=20)
    return "\n".join(lines)
