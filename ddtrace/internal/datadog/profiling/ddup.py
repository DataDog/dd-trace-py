from .utils import sanitize_string  # noqa: F401


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
        env: Optional[str],
        service: Optional[str],
        version: Optional[str],
        tags: Optional[Dict[str, str]],
        max_nframes: Optional[int],
        url: Optional[str],
    ):
        pass

    @not_implemented
    def start_sample(nframes: int) -> None:
        pass

    @not_implemented
    def push_cputime(value: int, count: int) -> None:
        pass

    @not_implemented
    def push_walltime(value: int, count: int) -> None:
        pass

    @not_implemented
    def push_acquire(value: int, count: int) -> None:
        pass

    @not_implemented
    def push_release(value: int, count: int) -> None:
        pass

    @not_implemented
    def push_alloc(value: int, count: int) -> None:
        pass

    @not_implemented
    def push_heap(value: int) -> None:
        pass

    @not_implemented
    def push_lock_name(lock_name: str) -> None:
        pass

    @not_implemented
    def push_frame(name: str, filename: str, address: int, line: int) -> None:
        pass

    @not_implemented
    def push_threadinfo(thread_id: int, thread_native_id: int, thread_name: Optional[str]) -> None:
        pass

    @not_implemented
    def push_taskinfo(task_id: int, task_name: str) -> None:
        pass

    @not_implemented
    def push_exceptioninfo(exc_type: type, count: int) -> None:
        pass

    @not_implemented
    def push_class_name(class_name: str) -> None:
        pass

    @not_implemented
    def push_span(span: Optional[Span], endpoint_collection_enabled: bool) -> None:
        pass

    @not_implemented
    def flush_sample() -> None:
        pass

    @not_implemented
    def upload() -> None:
        pass
