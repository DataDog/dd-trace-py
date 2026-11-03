import ddtrace.internal._unpatched  # noqa: F401, isort:skip

from ddtrace._logger import configure_ddtrace_logger
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate


configure_ddtrace_logger()


def check_supported_python_version() -> None:
    if not is_at_least_py(3, 10):
        deprecation_message = (
            "Support for ddtrace with Python version %d.%d is deprecated and will be removed in 5.0.0."
        )
        if not is_at_least_py(3, 9):
            deprecation_message = "Support for ddtrace with Python version %d.%d was removed in 4.0.0."
        deprecate(
            (deprecation_message % (PYTHON_VERSION_INFO[0], PYTHON_VERSION_INFO[1])),
            category=DDTraceDeprecationWarning,
        )


check_supported_python_version()

from ddtrace.internal.settings._config import config  # noqa: E402


if config._data_streams_enabled:
    import ddtrace.internal.datastreams  # noqa: E402, F401
