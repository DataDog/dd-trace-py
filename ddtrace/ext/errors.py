"""
tags for common error attributes
"""

import traceback

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import MSG
from ddtrace.constants import STACK
from ddtrace.constants import TYPE
from ddtrace.utils.deprecation import deprecated
from ddtrace.utils.deprecation import deprecation


__all__ = [ERROR_MSG, ERROR_TYPE, ERROR_STACK, MSG, TYPE, STACK]

deprecation(
    name="ddtrace.ext.errors",
    message="Use `ddtrace.constants` module instead",
    version="1.0.0",
)


@deprecated("This method and module will be removed altogether", "1.0.0")
def get_traceback(tb=None, error=None):
    t = None
    if error:
        t = type(error)
    lines = traceback.format_exception(t, error, tb, limit=20)
    return "\n".join(lines)
