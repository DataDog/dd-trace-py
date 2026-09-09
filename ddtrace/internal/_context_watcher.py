from contextvars import Context
from contextvars import ContextVar
from functools import wraps
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


def wrap_worker_context(func: Callable[..., Any]) -> Callable[..., Any]:
    """Publish the copied worker context on entry and an empty context on exit."""

    @wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        # Clear the delegation marker so nested worker boundaries remain visible.
        token = CONTEXT_SWITCH_WORKER_INSTRUMENTED.set(False)
        try:
            return func(*args, **kwargs)
        finally:
            CONTEXT_SWITCH_WORKER_INSTRUMENTED.reset(token)
            Context().run(core.dispatch, PYTHON_CONTEXT_SWITCH_EVENT)

    return wrapped
