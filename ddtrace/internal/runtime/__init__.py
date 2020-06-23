import os
import uuid

from .runtime_metrics import (
    RuntimeTags,
    RuntimeMetrics,
    RuntimeWorker,
)
from .. import _rand


__all__ = [
    "AT_FORK_INSTALLED",
    "RuntimeTags",
    "RuntimeMetrics",
    "RuntimeWorker",
    "get_runtime_id",
]


def _generate_runtime_id():
    return uuid.uuid4().hex


_RUNTIME_ID = _generate_runtime_id()


def ddtrace_at_fork():
    global _RUNTIME_ID

    _RUNTIME_ID = _generate_runtime_id()
    _rand.seed()


if hasattr(os, "register_at_fork"):

    def get_runtime_id(pid=None):
        return _RUNTIME_ID

    os.register_at_fork(after_in_child=ddtrace_at_fork)

    AT_FORK_INSTALLED = True


else:
    # Non-POSIX systems or Python < 3.7
    import threading

    _RUNTIME_PID = os.getpid()
    _RUNTIME_LOCK = threading.Lock()

    def get_runtime_id(pid=None):
        if pid is None:
            pid = os.getpid()

        # A lock is required here because it's possible that a process forks
        # and has a 2 or more threads that will make a call to get_runtime_id()
        # which will result in a race condition to update _RUNTIME_ID.
        with _RUNTIME_LOCK:
            global _RUNTIME_ID, _RUNTIME_PID

            if pid != _RUNTIME_PID:
                _RUNTIME_ID = _generate_runtime_id()
                _RUNTIME_PID = pid
            return _RUNTIME_ID

    AT_FORK_INSTALLED = False


get_runtime_id.__doc__ = """Return a unique string identifier for this runtime.

Do not store this identifier as it can change when, e.g., the process forks."""
