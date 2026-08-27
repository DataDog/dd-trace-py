from typing import Any

from ddtrace.internal.datadog.profiling import ddup


# Native profiling extensions may be absent on some Python versions (e.g. 3.15
# until setup.py gates are lifted). Mirror the is_available pattern used by
# ddtrace.internal.datadog.profiling.{ddup,stack}.
is_available: bool = False
failure_msg: str = ""

try:
    if not ddup.is_available:
        raise ImportError(ddup.failure_msg or "native profiling extensions are not built")

    from .profiler import Profiler  # noqa: F401

    is_available = True
except Exception as e:
    failure_msg = str(e)
    _profiler_import_error: BaseException = e

    class Profiler:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "ddtrace.profiling is not available on this Python version "
                "(native extensions are not built). "
                "Import ddtrace.profiling and check is_available/failure_msg for details."
            ) from _profiler_import_error
