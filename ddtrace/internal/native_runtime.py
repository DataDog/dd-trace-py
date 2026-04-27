import logging
from typing import Optional

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.native import SharedRuntime


log = logging.getLogger(__name__)

_DEFAULT_SHUTDOWN_TIMEOUT_MS = 3000


class NativeRuntime:
    """Manages a SharedRuntime with fork-safe lifecycle hooks.

    The SharedRuntime wraps a Tokio async runtime shared across TraceExporter
    instances. This class registers before_fork / after_fork_parent /
    after_fork_child hooks so the runtime is correctly paused and resumed
    around process forks. To pass the runtime to native components use `self.inner`.
    """

    def __init__(self) -> None:
        self.inner = SharedRuntime()
        forksafe.register_before_fork(self._before_fork)
        forksafe.register_after_parent(self._after_fork_parent)
        forksafe.register(self._after_fork_child)
        atexit.register(self._atexit)

    def _before_fork(self) -> None:
        self.inner.before_fork()

    def _after_fork_parent(self) -> None:
        self.inner.after_fork_parent()

    def _after_fork_child(self) -> None:
        self.inner.after_fork_child()

    def _atexit(self) -> None:
        try:
            self.inner.shutdown(timeout_ms=_DEFAULT_SHUTDOWN_TIMEOUT_MS)
        except Exception:
            log.debug("Error shutting down native runtime at exit", exc_info=True)

    def shutdown(self, timeout_ms: Optional[int] = None) -> None:
        """Shut down the shared Tokio runtime.

        Args:
            timeout_ms: Maximum time in milliseconds to wait for shutdown.
                If None, waits indefinitely — only safe if all workers have
                already been stopped (e.g. via TraceExporter.shutdown).
        """
        self.inner.shutdown(timeout_ms=timeout_ms)
        atexit.unregister(self._atexit)
        forksafe.unregister_before_fork(self._before_fork)
        forksafe.unregister_parent(self._after_fork_parent)
        forksafe.unregister(self._after_fork_child)


_instance: Optional[NativeRuntime] = None


def get_native_runtime() -> NativeRuntime:
    """Return the process-wide NativeRuntime singleton, creating it on first use.

    The first call also registers an atexit hook to shut the runtime down.
    """
    global _instance
    if _instance is None:
        _instance = NativeRuntime()
    return _instance
