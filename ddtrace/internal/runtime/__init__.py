import os
import uuid

from .runtime_metrics import (
    RuntimeTags,
    RuntimeMetrics,
    RuntimeWorker,
)


__all__ = [
    "RuntimeTags",
    "RuntimeMetrics",
    "RuntimeWorker",
    "get_runtime_id",
]


def _generate_runtime_id():
    return uuid.uuid4().hex


_RUNTIME_ID = _generate_runtime_id()


if hasattr(os, "register_at_fork"):

    def _set_runtime_id():
        global _RUNTIME_ID
        _RUNTIME_ID = _generate_runtime_id()

    os.register_at_fork(after_in_child=_set_runtime_id)

    def get_runtime_id():
        return _RUNTIME_ID


else:
    # Non-POSIX systems or Python < 3.7
    import threading

    _RUNTIME_PID = os.getpid()

    def _set_runtime_id():
        global _RUNTIME_ID, _RUNTIME_PID
        _RUNTIME_ID = _generate_runtime_id()
        _RUNTIME_PID = os.getpid()

    _RUNTIME_LOCK = threading.Lock()

    def get_runtime_id():
        with _RUNTIME_LOCK:
            pid = os.getpid()
            if pid != _RUNTIME_PID:
                _set_runtime_id()
            return _RUNTIME_ID


get_runtime_id.__doc__ = """Return a unique string identifier for this runtime.

Do not store this identifier as it can change when, e.g., the process forks."""
