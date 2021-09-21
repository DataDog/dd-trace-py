import sys
import warnings


def gevent_patch_all(event):
    if "ddtrace" in sys.modules:
        warnings.warn(
            "Loading ddtrace before using gevent monkey patching is not supported "
            "and is likely to break the application. "
            "Use `DD_GEVENT_PATCH_ALL=true ddtrace-run` to fix this or "
            "import `ddtrace` after `gevent.monkey.patch_all()` has been called.",
            RuntimeWarning,
        )
