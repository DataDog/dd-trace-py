from contextvars import Context
from contextvars import ContextVar
import sys
from typing import Any
from typing import Callable

from ddtrace.internal import core


PYTHON_CONTEXT_SWITCH_EVENT = "python.context.switch"
CONTEXT_SWITCH_WORKER_INSTRUMENTED = ContextVar("context_switch_worker_instrumented", default=False)


if sys.implementation.name == "cpython" and sys.version_info >= (3, 14):
    from ddtrace.internal.native._native import is_context_watcher_registered as is_context_watcher_registered
    from ddtrace.internal.native._native import register_context_watcher as register_context_watcher

else:

    def is_context_watcher_registered() -> bool:
        return False

    def register_context_watcher() -> bool:
        return False


def context_switches_require_fallback() -> bool:
    """Whether integrations must publish context switches that the native watcher cannot observe."""
    return core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT) and not is_context_watcher_registered()


def copy_identity(wrapped: Callable[..., Any], wrapper: Callable[..., Any]) -> None:
    """Copy the wrapped callable's name onto wrapper, unlike functools.wraps, only when present.

    functools.wraps unconditionally copies __module__/__doc__/__dict__ and sets __wrapped__, which
    changes a plain function's introspection and is wasted work here: callers only rely on the
    thread/task name Trio derives from __name__, and only when the wrapped callable has one (a
    functools.partial does not).
    """
    for attr in ("__name__", "__qualname__"):
        value = getattr(wrapped, attr, None)
        if value is not None:
            setattr(wrapper, attr, value)


def wrap_worker_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Publish the copied worker context on entry and an empty context on exit."""

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
            core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        # Clear the delegation marker so nested worker boundaries remain visible.
        token = CONTEXT_SWITCH_WORKER_INSTRUMENTED.set(False)
        try:
            return func(*args, **kwargs)
        finally:
            CONTEXT_SWITCH_WORKER_INSTRUMENTED.reset(token)
            if core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT):
                Context().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    copy_identity(func, wrapped)
    return wrapped
