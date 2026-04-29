import logging
from typing import Optional

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.native import SharedRuntime


log = logging.getLogger(__name__)

_DEFAULT_SHUTDOWN_TIMEOUT_MS = 3000


class NativeRuntime(SharedRuntime):
    """Manages a SharedRuntime with fork-safe lifecycle hooks.

    The SharedRuntime wraps a Tokio async runtime shared across TraceExporter
    instances. This class registers before_fork / after_fork_parent /
    after_fork_child hooks so the runtime is correctly paused and resumed
    around process forks.
    """

    _instance: Optional["NativeRuntime"] = None

    def __init__(self) -> None:
        super().__init__()
        forksafe.register_before_fork(self.before_fork)
        forksafe.register_after_parent(self.after_fork_parent)
        forksafe.register(self.after_fork_child)
        atexit.register(self._atexit)

    def _atexit(self) -> None:
        try:
            self.shutdown(timeout_ms=_DEFAULT_SHUTDOWN_TIMEOUT_MS)
        except Exception:
            log.debug("Error shutting down native runtime at exit", exc_info=True)

    def shutdown(self, timeout_ms: Optional[int] = None) -> None:
        """Shut down the shared Tokio runtime.

        Args:
            timeout_ms: Maximum time in milliseconds to wait for shutdown.
                If None, waits indefinitely — only safe if all workers have
                already been stopped (e.g. via TraceExporter.shutdown).
        """
        super().shutdown(timeout_ms=timeout_ms)
        atexit.unregister(self._atexit)
        forksafe.unregister_before_fork(self.before_fork)
        forksafe.unregister_parent(self.after_fork_parent)
        forksafe.unregister(self.after_fork_child)


def get_native_runtime() -> NativeRuntime:
    """Return the process-wide NativeRuntime singleton, creating it on first use.

    The first call also registers an atexit hook to shut the runtime down.
    """
    if NativeRuntime._instance is None:
        NativeRuntime._instance = NativeRuntime()
    return NativeRuntime._instance
