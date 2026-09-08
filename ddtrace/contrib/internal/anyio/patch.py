"""Publish context switches around AnyIO worker-thread callables."""

from contextvars import Context
from functools import partial
from importlib.metadata import version
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import cast

import anyio
import anyio.to_thread

from ddtrace.internal import core
from ddtrace.internal._context_watcher import CONTEXT_SWITCH_WORKER_INSTRUMENTED
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal._context_watcher import context_switches_require_fallback
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


def get_version() -> str:
    return version("anyio")


def _supported_versions() -> dict[str, str]:
    return {"anyio": ">=3.4.0"}


def patch() -> None:
    """Patch AnyIO worker calls when the native context watcher is unavailable."""
    if getattr(anyio, "_datadog_patch", False) or not context_switches_require_fallback():
        return

    wrap(anyio.to_thread.run_sync, _wrapped_run_sync)
    anyio._datadog_patch = True


def unpatch() -> None:
    """Remove AnyIO worker-call instrumentation."""
    if not getattr(anyio, "_datadog_patch", False):
        return

    unwrap(anyio.to_thread.run_sync, _wrapped_run_sync)
    anyio._datadog_patch = False


def _run_with_context_switches(func: Callable[..., Any], *args: Any) -> Any:
    """Publish the copied worker context and the empty context restored afterwards."""
    core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
    try:
        return func(*args)
    finally:
        Context().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)


async def _wrapped_run_sync(
    wrapped: Callable[..., Awaitable[Any]], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    func = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "func"))
    args, kwargs = set_argument_value(args, kwargs, 0, "func", partial(_run_with_context_switches, func))
    token = CONTEXT_SWITCH_WORKER_INSTRUMENTED.set(True)
    try:
        return await wrapped(*args, **kwargs)
    finally:
        CONTEXT_SWITCH_WORKER_INSTRUMENTED.reset(token)
