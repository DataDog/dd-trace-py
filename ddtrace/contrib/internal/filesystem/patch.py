import builtins
from collections.abc import Callable
from inspect import CO_VARARGS
from types import FunctionType
from typing import Any
from typing import Optional

from wrapt import FunctionWrapper

from ddtrace.contrib._events.filesystem import FileOpenEvent
from ddtrace.internal import core
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.hooks import try_unwrap_context
from ddtrace.internal.wrapping.hooks import try_wrap_context


_open_patch: Optional["_OpenPatch"] = None


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"filesystem": "*"}


def wrapped_builtin_open(
    original: Callable[..., Any], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    try:
        if core.has_listeners(FileOpenEvent.event_name):
            core.dispatch_event(
                FileOpenEvent(path=args[0] if args else kwargs.get("file"), stack_trace_boundary="wrapped_builtin_open")
            )
    except Exception:  # nosec B110: listener errors must not affect application file operations.
        pass
    return original(*args, **kwargs)


class _PathOpenContext(WrappingContext):
    def __init__(self, wrapped: FunctionType) -> None:
        super().__init__(wrapped)
        code = wrapped.__code__
        self._variadic_receiver = code.co_argcount == 0
        if self._variadic_receiver and not code.co_flags & CO_VARARGS:
            raise TypeError("Path.open must accept a positional receiver")
        receiver_index = code.co_kwonlyargcount if self._variadic_receiver else 0
        self._receiver_name = code.co_varnames[receiver_index]
        self._stack_trace_boundary = code.co_name

    def __enter__(self) -> "_PathOpenContext":
        super().__enter__()
        try:
            if core.has_listeners(FileOpenEvent.event_name):
                receiver = self.__frame__.f_locals[self._receiver_name]
                if self._variadic_receiver:
                    receiver = receiver[0]
                core.dispatch_event(FileOpenEvent(path=receiver, stack_trace_boundary=self._stack_trace_boundary))
        except Exception:  # nosec B110: listener errors must not affect application file operations.
            pass
        return self


class _OpenPatch:
    def __init__(self) -> None:
        self.active = True
        # builtins.open is implemented in C, so it cannot be bytecode-wrapped.
        self.wrapper = FunctionWrapper(builtins.open, wrapped_builtin_open, enabled=self.is_active)
        # Preserve the existing hook's copy behavior for configuration containing open().
        self.wrapper.__deepcopy__ = lambda memo: self.wrapper

    def is_active(self) -> bool:
        return self.active


def patch() -> None:
    global _open_patch
    if _open_patch is not None:
        return

    telemetry_writer.add_passthrough_wrapper(wrapped_builtin_open)
    _open_patch = _OpenPatch()
    builtins.open = _open_patch.wrapper
    try_wrap_context("pathlib", "Path.open", _PathOpenContext)


def unpatch() -> None:
    global _open_patch
    if _open_patch is None:
        return

    # Aliases and ordinary Python decorators can retain our wrapper after unpatch.
    # Each installation has its own switch so re-patching cannot reactivate them.
    _open_patch.active = False
    wrapper = _open_patch.wrapper
    # Remove only our layer, preserving wrappers another consumer installed later.
    current = builtins.open
    if current is wrapper:
        builtins.open = wrapper.__wrapped__
    else:
        while isinstance(current, FunctionWrapper):
            if current.__wrapped__ is wrapper:
                current.__wrapped__ = wrapper.__wrapped__
                break
            current = current.__wrapped__
    _open_patch = None
    try_unwrap_context("pathlib", "Path.open")
