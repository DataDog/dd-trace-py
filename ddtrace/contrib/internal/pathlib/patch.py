import os
import pathlib
from typing import TYPE_CHECKING
from typing import Any

from wrapt import wrap_function_wrapper as _w

from ddtrace.contrib._events.file_io import FileOpenEvent
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils.wrappers import crop_previous_frame


if TYPE_CHECKING:
    from typing import IO
    from typing import Callable


config._add("pathlib", dict())


def get_version() -> str:
    return ""


def _supported_versions() -> dict[str, str]:
    return {"pathlib": "*"}


def wrapped_path_open(
    original_method_callable: "Callable[..., IO]",
    instance: "pathlib.Path",
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> "IO":
    """
    wrapper for pathlib.Path.open() method
    """
    if core.has_listeners(FileOpenEvent.event_name):
        try:
            filename = os.fspath(instance)
        except Exception:
            filename = None

        if filename is not None:
            core.dispatch_event(FileOpenEvent(filename=filename))

    try:
        return original_method_callable(*args, **kwargs)
    except Exception as e:
        raise crop_previous_frame(e)


def patch() -> None:
    if getattr(pathlib, "__datadog_patch", False):
        return
    pathlib.__datadog_patch = True  # type: ignore[attr-defined]

    _w("pathlib", "Path.open", wrapped_path_open)


def unpatch() -> None:
    if not getattr(pathlib, "__datadog_patch", False):
        return
    pathlib.__datadog_patch = False  # type: ignore[attr-defined]

    _u(pathlib.Path, "open")
