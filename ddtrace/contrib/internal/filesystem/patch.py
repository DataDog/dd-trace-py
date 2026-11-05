import builtins
from collections.abc import Callable
from contextlib import suppress
from typing import Any
from typing import Optional

from wrapt import FunctionWrapper

from ddtrace.contrib._events.filesystem import FileOpenEvent
from ddtrace.internal import core
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import try_unwrap_context
from ddtrace.internal.wrapping.hooks import try_wrap_context


_open_wrapper: Optional[FunctionWrapper] = None


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"filesystem": "*"}


def wrapped_builtin_open(
    original: Callable[..., Any], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    with suppress(Exception):
        if core.has_listeners(FileOpenEvent.event_name):
            core.dispatch_event(FileOpenEvent(path=args[0] if args else kwargs.get("file")))
    return original(*args, **kwargs)


class _PathOpenContext(WrappingContext):
    def __enter__(self) -> "_PathOpenContext":
        super().__enter__()
        with suppress(Exception):
            if core.has_listeners(FileOpenEvent.event_name):
                core.dispatch_event(FileOpenEvent(path=self.__frame__.f_locals["self"]))
        return self


def patch() -> None:
    global _open_wrapper
    if _open_wrapper is not None:
        return

    telemetry_writer.add_passthrough_wrapper(wrapped_builtin_open)
    # builtins.open is implemented in C, so it cannot be bytecode-wrapped.
    wrapper = FunctionWrapper(builtins.open, wrapped_builtin_open)
    # Preserve the existing filesystem hook's copy behavior for callers that
    # deepcopy configuration containing open().
    wrapper.__deepcopy__ = lambda memo: wrapper
    builtins.open = _open_wrapper = wrapper
    try_wrap_context("pathlib", "Path.open", _PathOpenContext)


def unpatch() -> None:
    global _open_wrapper
    if _open_wrapper is None:
        return

    # Remove only our layer, preserving wrappers another consumer installed later.
    current = builtins.open
    if current is _open_wrapper:
        builtins.open = _open_wrapper.__wrapped__
    else:
        while isinstance(current, FunctionWrapper):
            if current.__wrapped__ is _open_wrapper:
                current.__wrapped__ = _open_wrapper.__wrapped__
                break
            current = current.__wrapped__
    _open_wrapper = None
    try_unwrap_context("pathlib", "Path.open")
