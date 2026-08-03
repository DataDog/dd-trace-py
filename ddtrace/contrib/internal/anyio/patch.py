from contextvars import Context
from functools import partial
from importlib.metadata import version
import sys
from typing import Any
from typing import Callable
from typing import cast

import anyio
import anyio.to_thread

from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


_CONTEXT_SWITCH_EVENT = "python.context.switch"


def get_version() -> str:
    return version("anyio")


def _supported_versions() -> dict[str, str]:
    return {"anyio": ">=3.4.0"}


def patch() -> None:
    if getattr(anyio, "_datadog_patch", False):
        return

    anyio._datadog_patch = True
    # CPython 3.14+ publishes context switches through the native watcher, so
    # wrapping worker functions there would only add overhead.
    if sys.implementation.name != "cpython" or sys.version_info < (3, 14):
        wrap(anyio.to_thread.run_sync, _wrapped_run_sync)


def unpatch() -> None:
    if not getattr(anyio, "_datadog_patch", False):
        return

    anyio._datadog_patch = False
    if sys.implementation.name != "cpython" or sys.version_info < (3, 14):
        unwrap(anyio.to_thread.run_sync, _wrapped_run_sync)


def _run_sync(func: Callable[..., Any], *args: Any) -> Any:
    core.dispatch(_CONTEXT_SWITCH_EVENT)
    try:
        return func(*args)
    finally:
        Context().run(core.dispatch, _CONTEXT_SWITCH_EVENT)


def _wrapped_run_sync(
    wrapped: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    if not core.has_listeners(_CONTEXT_SWITCH_EVENT):
        return wrapped(*args, **kwargs)

    func = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "func"))
    args, kwargs = set_argument_value(args, kwargs, 0, "func", partial(_run_sync, func))
    return wrapped(*args, **kwargs)
