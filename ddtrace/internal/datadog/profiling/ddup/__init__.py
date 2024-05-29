from .utils import sanitize_string  # noqa: F401


try:
    from ._ddup import *  # noqa: F403, F401

    is_available = True

except Exception as e:
    from typing import Dict  # noqa:F401
    from typing import Optional  # noqa:F401

    from ddtrace.internal.logger import get_logger

    LOG = get_logger(__name__)
    LOG.debug("Failed to import _ddup: %s", e)

    is_available = False

    # Decorator for not-implemented
    def not_implemented(func):
        def wrapper(*args, **kwargs):
            raise NotImplementedError("{} is not implemented on this platform".format(func.__name__))

    @not_implemented
    def init(
        env,  # type: Optional[str]
        service,  # type: Optional[str]
        version,  # type: Optional[str]
        tags,  # type: Optional[Dict[str, str]]
        max_nframes,  # type: Optional[int]
        url,  # type: Optional[str]
    ):
        pass

    @not_implemented
    def upload():  # type: () -> None
        pass

    class SampleHandle:
        @not_implemented
        def push_cputime(self, value, count):  # type: (int, int) -> None
            pass

        @not_implemented
        def push_walltime(self, value, count):  # type: (int, int) -> None
            pass

        @not_implemented
        def push_acquire(self, value, count):  # type: (int, int) -> None
            pass

        @not_implemented
        def push_release(self, value, count):  # type: (int, int) -> None
            pass

        @not_implemented
        def push_alloc(self, value, count):  # type: (int, int) -> None
            pass

        @not_implemented
        def push_heap(self, value):  # type: (int) -> None
            pass

        @not_implemented
        def push_lock_name(self, lock_name):  # type: (str) -> None
            pass

        @not_implemented
        def push_frame(self, name, filename, address, line):  # type: (str, str, int, int) -> None
            pass

        @not_implemented
        def push_threadinfo(self, thread_id, thread_native_id, thread_name):  # type: (int, int, Optional[str]) -> None
            pass

        @not_implemented
        def push_taskinfo(self, task_id, task_name):  # type: (int, str) -> None
            pass

        @not_implemented
        def push_exceptioninfo(self, exc_type, count):  # type: (type, int) -> None
            pass

        @not_implemented
        def push_class_name(self, class_name):  # type: (str) -> None
            pass

        @not_implemented
        def push_span(self, span, endpoint_collection_enabled):  # type: (Optional[Span], bool) -> None
            pass

        @not_implemented
        def flush_sample(self):  # type: () -> None
            pass
