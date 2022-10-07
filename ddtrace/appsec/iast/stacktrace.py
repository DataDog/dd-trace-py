import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Text
    from typing import Tuple


def get_info_frame():
    # type: () -> Tuple[Text, int]
    """Get the filename (path + filename) and line number of the original wrapped function to report it.

    CAVEAT: We should migrate this function to native code to improve the performance.
    """
    import inspect

    stack = inspect.stack()
    for frame in stack[4:]:
        if sys.version_info < (3, 0, 0):
            filename = frame[1]
            lineno = frame[2]
        else:
            filename = frame.filename
            lineno = frame.lineno
        if "ddtrace" in filename and "tests" not in filename:
            continue

        return filename, lineno

    return "", 0
