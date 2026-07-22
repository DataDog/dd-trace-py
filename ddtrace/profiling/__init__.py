from typing import Any


# Native profiling extensions may be absent on some Python versions (e.g. 3.15
# until setup.py gates are lifted). Mirror the is_available pattern used by
# ddtrace.internal.datadog.profiling.{ddup,stack}.
is_available = False
failure_msg = ""

try:
    from .profiler import Profiler  # noqa: F401

    is_available = True
except Exception as e:
    failure_msg = str(e)
    _profiler_import_error = e

    class Profiler:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "ddtrace.profiling is not available on this Python version "
                "(native extensions are not built). "
                "Import ddtrace.profiling and check is_available/failure_msg for details."
            ) from _profiler_import_error
