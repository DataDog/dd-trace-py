from typing import Any
from typing import Optional


# Native profiling extensions may be absent on some Python versions (e.g. 3.15
# until setup.py gates are lifted). Mirror the is_available pattern used by
# ddtrace.internal.datadog.profiling.{ddup,stack}.
is_available: bool = False
failure_msg: str = ""


class _UnavailableProfiler:
    """Stub Profiler class used when native profiling extensions are not available.

    Interface matches the public interface of ``ddtrace.profiling.profiler.Profiler``
    so static type checking and introspection stay consistent whether or not the
    Profiler is actually available.
    """

    _import_error: Optional[BaseException] = None
    _msg: str = (
        "ddtrace.profiling is not available on this Python version "
        "(native extensions are not built). "
        "Import ddtrace.profiling and check is_available/failure_msg for details."
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise ImportError(self._msg) from type(self)._import_error

    def start(self) -> None:
        raise ImportError(self._msg) from type(self)._import_error

    def stop(self, flush: bool = True) -> None:
        raise ImportError(self._msg) from type(self)._import_error

    def __getattr__(self, key: str) -> Any:
        raise ImportError(self._msg) from type(self)._import_error


try:
    from .profiler import Profiler  # noqa: F401

    is_available = True
except Exception as e:
    failure_msg = str(e)
    _UnavailableProfiler._import_error = e

    class Profiler(_UnavailableProfiler):  # type: ignore[no-redef]
        pass
