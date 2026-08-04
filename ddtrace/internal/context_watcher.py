from contextvars import Context
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import TypeVar

from ddtrace.internal import core
from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.native import _native


T = TypeVar("T")


def is_context_watcher_registered() -> bool:
    """Return whether this process is publishing native context switches."""
    is_registered = getattr(_native, "is_context_watcher_registered", None)
    return bool(is_registered()) if is_registered is not None else False


def _run_with_context_switches(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Publish the copied worker context on entry and detach native state on exit.

    The worker runtime invokes this helper inside its copied Context. The final
    dispatch must run in a fresh Context so the worker thread does not retain
    the completed call's native trace and span correlation.
    """
    core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
    try:
        return func(*args, **kwargs)
    finally:
        Context().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)


async def _await_with_context_switch(awaitable: Awaitable[T]) -> T:
    core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
    return await awaitable
