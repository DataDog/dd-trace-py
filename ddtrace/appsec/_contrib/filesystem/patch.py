import os
from typing import Protocol

from ddtrace.appsec._contrib.filesystem.events import FileOpenEvent
from ddtrace.appsec._patch_utils import _raise_without_wrapper_frame
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.internal import core


class _OpenCallable(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...


class _PathOpenReceiver(Protocol):
    def __fspath__(self) -> str: ...


def wrapped_builtin_open(
    original: _OpenCallable,
    _instance: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object:
    if core.has_listeners(FileOpenEvent.event_name):
        value = args[0] if args else kwargs.get("file")
        filename = None
        if isinstance(value, (str, bytes, os.PathLike)):
            try:
                filename = os.fspath(value)
            except Exception:
                filename = None
        if filename:
            core.dispatch_event(FileOpenEvent(filename=filename), allow_raise=True)

    try:
        return original(*args, **kwargs)
    except Exception as exc:
        raise _raise_without_wrapper_frame(exc)


def wrapped_path_open(
    original: _OpenCallable,
    instance: _PathOpenReceiver,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object:
    if core.has_listeners(FileOpenEvent.event_name):
        try:
            filename = os.fspath(instance)
        except Exception:
            filename = None
        if filename:
            core.dispatch_event(FileOpenEvent(filename=filename), allow_raise=True)

    try:
        return original(*args, **kwargs)
    except Exception as exc:
        raise _raise_without_wrapper_frame(exc)


def patch() -> None:
    try_wrap_function_wrapper("builtins", "open", wrapped_builtin_open)
    try_wrap_function_wrapper("pathlib", "Path.open", wrapped_path_open)


def unpatch() -> None:
    try_unwrap("builtins", "open")
    try_unwrap("pathlib", "Path.open")
