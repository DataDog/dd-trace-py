import sys

from ddtrace.internal import core


PYTHON_CONTEXT_SWITCH_EVENT = "python.context.switch"


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
