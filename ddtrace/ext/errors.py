"""
tags for common error attributes
"""

import traceback

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.utils.deprecation import deprecated
from ddtrace.internal.utils.deprecation import deprecation


__all__ = [ERROR_MSG, ERROR_TYPE, ERROR_STACK]

deprecation(
    name="ddtrace.ext.errors",
    message=(
        "Use `ddtrace.constants` module instead. "
        "Shorthand error constants will be removed. Use ERROR_MSG, ERROR_TYPE, and ERROR_STACK instead."
    ),
    version="1.0.0",
)

# shorthand for ERROR constants to be removed in v1.0-----^
MSG = ERROR_MSG
TYPE = ERROR_TYPE
STACK = ERROR_STACK


@deprecated("This method and module will be removed altogether", "1.0.0")
def get_traceback(tb=None, error=None):
    t = None
    if error:
        t = type(error)
    lines = traceback.format_exception(t, error, tb, limit=20)
    return "\n".join(lines)
