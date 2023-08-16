try:
    from ._ddup import *  # noqa: F403, F401
except ImportError:
    from typing import Dict
    from typing import Optional

    from ddtrace.span import Span

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
    def start_sample(nframes):  # type: (int) -> None
        pass

    @not_implemented
    def push_cputime(value, count):  # type: (int, int) -> None
        pass

    @not_implemented
    def push_walltime(value, count):  # type: (int, int) -> None
        pass

    @not_implemented
    def push_acquire(value, count):  # type: (int, int) -> None
        pass

    @not_implemented
    def push_release(value, count):  # type: (int, int) -> None
        pass

    @not_implemented
    def push_alloc(value, count):  # type: (int, int) -> None
        pass

    @not_implemented
    def push_heap(value):  # type: (int) -> None
        pass

    @not_implemented
    def push_lock_name(lock_name):  # type: (str) -> None
        pass

    @not_implemented
    def push_frame(name, filename, address, line):  # type: (str, str, int, int) -> None
        pass

    @not_implemented
    def push_threadinfo(thread_id, thread_native_id, thread_name):  # type: (int, int, Optional[str]) -> None
        pass

    @not_implemented
    def push_taskinfo(task_id, task_name):  # type: (int, str) -> None
        pass

    @not_implemented
    def push_exceptioninfo(exc_type, count):  # type: (type, int) -> None
        pass

    @not_implemented
    def push_class_name(class_name):  # type: (str) -> None
        pass

    @not_implemented
    def push_span(span, endpoint_collection_enabled):  # type: (Optional[Span], bool) -> None
        pass

    @not_implemented
    def flush_sample():  # type: () -> None
        pass

    @not_implemented
    def upload():  # type: () -> None
        pass
