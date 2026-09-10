"""Publish context switches around AnyIO worker-thread callables."""

from importlib.metadata import version
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import cast

import anyio
import anyio.to_thread

from ddtrace.internal._context_watcher import CONTEXT_SWITCH_WORKER_INSTRUMENTED
from ddtrace.internal._context_watcher import context_switches_require_fallback
from ddtrace.internal._context_watcher import wrap_worker_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


def get_version() -> str:
    return version("anyio")


def _supported_versions() -> dict[str, str]:
    return {"anyio": ">=3.4.0"}


_installed = False


def patch() -> None:
    """Patch AnyIO, installing the fallback hook only when the native context watcher can't cover it."""
    global _installed
    if getattr(anyio, "_datadog_patch", False):
        return

    if not _installed and context_switches_require_fallback():
        wrap(anyio.to_thread.run_sync, _wrapped_run_sync)
        _installed = True
    anyio._datadog_patch = True


def unpatch() -> None:
    """Remove AnyIO worker-call instrumentation."""
    global _installed
    if not getattr(anyio, "_datadog_patch", False):
        return

    if _installed:
        unwrap(anyio.to_thread.run_sync, _wrapped_run_sync)
        _installed = False
    anyio._datadog_patch = False


async def _wrapped_run_sync(
    wrapped: Callable[..., Awaitable[Any]], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Own AnyIO-to-backend suppression while the shared wrapper owns worker publication."""
    func = cast(Callable[..., Any], get_argument_value(args, kwargs, 0, "func"))
    args, kwargs = set_argument_value(args, kwargs, 0, "func", wrap_worker_context(func))
    token = CONTEXT_SWITCH_WORKER_INSTRUMENTED.set(True)
    try:
        return await wrapped(*args, **kwargs)
    finally:
        CONTEXT_SWITCH_WORKER_INSTRUMENTED.reset(token)
