import inspect
import os
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Optional  # noqa:F401
    from typing import Text  # noqa:F401
    from typing import Tuple  # noqa:F401


FIRST_FRAME_NO_DDTRACE = 1

DD_TRACE_INSTALLED_PREFIX = os.sep + "ddtrace" + os.sep
SITE_PACKAGES_PREFIX = os.sep + "site-packages" + os.sep
TESTS_PREFIX = os.sep + "tests" + os.sep


def get_info_frame(cwd):
    # type: (Text) -> Optional[Tuple[Text, int, Text, Text]]
    """Get the filename (path + filename) and line number of the original wrapped function to report it.

    CAVEAT: We should migrate this function to native code to improve the performance.
    """
    stack = inspect.stack()
    for frame in stack[FIRST_FRAME_NO_DDTRACE:]:
        filename = frame.filename
        lineno = frame.lineno
        function_name = frame.function
        instance = frame.frame.f_locals.get("self")
        class_name = instance.__class__.__name__ if instance else None

        if (
            (DD_TRACE_INSTALLED_PREFIX in filename and TESTS_PREFIX not in filename)
            or (cwd not in filename)
            or (SITE_PACKAGES_PREFIX in filename)
        ):
            continue

        return filename, lineno, function_name, class_name

    return None
