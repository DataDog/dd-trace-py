from typing import Any
from typing import Callable
from typing import Generic
from typing import Optional
from typing import TypeVar
from typing import Union

from ddtrace.internal._unpatched import threading_Lock
from ddtrace.internal.logger import get_logger


LOGGER = get_logger(__name__)


T = TypeVar("T")


DDWafRulesType = Union[None, int, str, list[Any], dict[str, Any]]


class ddwaf_handle_capsule(Generic[T]):
    def __init__(self, handle: type[T], free_fn: Callable[[type[T]], None]) -> None:
        self.handle: Optional[type[T]] = handle
        self.free_fn = free_fn

    def __del__(self) -> None:
        if self.handle:
            try:
                self.free_fn(self.handle)
            except TypeError:
                LOGGER.debug("Failed to free handle", exc_info=True)
            self.handle = None

    def __bool__(self) -> bool:
        return bool(self.handle)


class ddwaf_context_capsule(Generic[T]):
    def __init__(self, ctx: type[T], free_fn: Callable[[type[T]], None]) -> None:
        self.ctx: Optional[type[T]] = ctx
        self.free_fn = free_fn
        self.rc_products: str = ""
        # AIDEV-NOTE: This lock serializes concurrent ddwaf_run calls on the same context.
        # ctypes foreign function calls release the Python GIL, allowing thread pool workers
        # (run_in_executor) to call the WAF simultaneously with the event loop thread when both
        # inherit the same ddwaf_context via contextvars propagation. libddwaf's ddwaf_context
        # is not thread-safe for concurrent runs, so we must serialize them here.
        self._lock: threading_Lock = threading_Lock()

    def __del__(self) -> None:
        if self.ctx:
            try:
                self.free_fn(self.ctx)
            except TypeError:
                LOGGER.debug("Failed to free context", exc_info=True)
            self.ctx = None

    def __bool__(self) -> bool:
        return bool(self.ctx)


class ddwaf_builder_capsule(Generic[T]):
    def __init__(self, builder: type[T], free_fn: Callable[[type[T]], None]) -> None:
        self.builder: Optional[type[T]] = builder
        self.free_fn = free_fn

    def __del__(self) -> None:
        if self.builder:
            try:
                self.free_fn(self.builder)
            except TypeError:
                LOGGER.debug("Failed to free builder", exc_info=True)
            self.builder = None

    def __bool__(self) -> bool:
        return bool(self.builder)
