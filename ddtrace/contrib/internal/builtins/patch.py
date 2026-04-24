import builtins
import os
from typing import IO
from typing import Any
from typing import Callable

from wrapt import wrap_function_wrapper as _w

from ddtrace.contrib._events.file_io import FileOpenEvent
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils.wrappers import crop_previous_frame


config._add("builtins", dict())


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"builtins": "*"}


def wrapped_open(
    original_open_callable: "Callable[..., IO]", instance: None, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> "IO":
    """
    wrapper for builtins.open file function
    """
    if core.has_listeners(FileOpenEvent.event_name):
        filename_arg = args[0] if args else kwargs.get("file", None)

        if filename_arg is not None:
            try:
                filename = os.fspath(filename_arg)
            except Exception:
                filename = None

            if filename is not None:
                core.dispatch_event(FileOpenEvent(filename=filename))

    try:
        return original_open_callable(*args, **kwargs)
    except Exception as e:
        raise crop_previous_frame(e)


def patch() -> None:
    if getattr(builtins, "__datadog_patch", False):
        return
    builtins.__datadog_patch = True  # type: ignore[attr-defined]

    _w("builtins", "open", wrapped_open)
    # builtins.open may be deepcopied (e.g. by multiprocessing), the wrapt proxy
    # must support it to avoid NotImplementedError.
    builtins.open.__deepcopy__ = lambda memo: builtins.open  # type: ignore[union-attr]


def unpatch() -> None:
    if not getattr(builtins, "__datadog_patch", False):
        return
    builtins.__datadog_patch = False  # type: ignore[attr-defined]

    _u(builtins, "open")
