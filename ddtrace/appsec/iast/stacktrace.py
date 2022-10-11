import inspect
import os
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Text
    from typing import Tuple


FIRST_FRAME_NO_DDTRACE = 4

DD_TRACE_INSTALLED_PREFIX = os.sep + "ddtrace" + os.sep
TESTS_PREFIX = os.sep + "tests" + os.sep


def get_info_frame():
    # type: () -> Tuple[Text, int]
    """Get the filename (path + filename) and line number of the original wrapped function to report it.

    CAVEAT: We should migrate this function to native code to improve the performance.
    """
    stack = inspect.stack()
    for frame in stack[FIRST_FRAME_NO_DDTRACE:]:
        if sys.version_info < (3, 0, 0):
            filename = frame[1]
            lineno = frame[2]
        else:
            filename = frame.filename
            lineno = frame.lineno
        if DD_TRACE_INSTALLED_PREFIX in filename and TESTS_PREFIX not in filename:
            continue

        return filename, lineno

    return "", 0
