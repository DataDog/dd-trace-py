from ddtrace.internal.native import _native
from ddtrace.internal.settings._config import config


def register_context_watcher() -> bool:
    """Register the native context watcher when this runtime supports it."""
    if not config._python_context_watcher_enabled:
        return False

    register = getattr(_native, "register_context_watcher", None)
    return bool(register()) if register is not None else False


def is_context_watcher_registered() -> bool:
    """Return whether this process is publishing native context switches."""
    if not config._python_context_watcher_enabled:
        return False

    is_registered = getattr(_native, "is_context_watcher_registered", None)
    return bool(is_registered()) if is_registered is not None else False
