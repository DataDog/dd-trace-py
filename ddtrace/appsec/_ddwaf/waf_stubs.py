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
        # Serializes concurrent ddwaf_context_eval calls on the same context: ctypes releases the
        # GIL, so thread-pool workers and the event loop can hit a shared context at once, which
        # libddwaf does not allow.
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


class ddwaf_subcontext_capsule(Generic[T]):
    # Subcontexts (libddwaf 2.0) evaluate non-persisting RASP data, inheriting the parent
    # context's persistent data. In v2.0 a subcontext is independent of its parent context (the
    # context may be destroyed first; shared objects are reference counted), so we don't need to
    # keep a reference to the parent. Own lock to serialize eval/destroy on the same subcontext.
    def __init__(self, subctx: type[T], free_fn: Callable[[type[T]], None]) -> None:
        self.subctx: Optional[type[T]] = subctx
        self.free_fn = free_fn
        self._lock: threading_Lock = threading_Lock()

    def __del__(self) -> None:
        if self.subctx:
            try:
                self.free_fn(self.subctx)
            except TypeError:
                LOGGER.debug("Failed to free subcontext", exc_info=True)
            self.subctx = None

    def __bool__(self) -> bool:
        return bool(self.subctx)


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
