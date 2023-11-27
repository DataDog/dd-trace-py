import inspect
import os
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Optional
    from typing import Text
    from typing import Tuple


FIRST_FRAME_NO_DDTRACE = 1

DD_TRACE_INSTALLED_PREFIX = os.sep + "ddtrace" + os.sep
SITE_PACKAGES_PREFIX = os.sep + "site-packages" + os.sep
TESTS_PREFIX = os.sep + "tests" + os.sep


def get_info_frame(cwd):
    # type: (Text) -> Optional[Tuple[Text, int]]
    """Get the filename (path + filename) and line number of the original wrapped function to report it.

    CAVEAT: We should migrate this function to native code to improve the performance.
    """
    stack = inspect.stack()
    for frame in stack[FIRST_FRAME_NO_DDTRACE:]:
        filename = frame.filename
        lineno = frame.lineno
        if (
            (DD_TRACE_INSTALLED_PREFIX in filename and TESTS_PREFIX not in filename)
            or (cwd not in filename)
            or (SITE_PACKAGES_PREFIX in filename)
        ):
            continue

        return filename, lineno

    return None
