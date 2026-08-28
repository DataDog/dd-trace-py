from functools import cache
import logging
import sys
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

    def __init__(self) -> None:
        super().__init__()
        # True from before_fork until whichever of after_fork_parent/after_fork_child runs. No
        # runtime exists in that window, so a blocking flush issued from a fork hook (this one's
        # own, or another product's, since forksafe dispatches every registered hook in order) would
        # wait on a condvar nothing can ever notify and hang os.fork() forever. Anything that wants
        # to flush should check this first and fall back to a fire-and-forget flush instead.
        self._paused = False
        forksafe.register_before_fork(self.before_fork)
        forksafe.register_after_parent(self.after_fork_parent)
        forksafe.register(self.after_fork_child)
        atexit.register(self._atexit)
        atexit.register_on_exit_signal(self._atexit)

    def before_fork(self) -> None:
        # Set before the super call: that call is what pauses (and, on the last worker, drops) the
        # runtime, so _paused must already be true by the time any of it can observe.
        self._paused = True
        super().before_fork()

    def after_fork_parent(self) -> None:
        super().after_fork_parent()
        self._paused = False

    def after_fork_child(self) -> None:
        super().after_fork_child()
        self._paused = False

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
        if "uwsgi" in sys.modules:
            super().shutdown_in_thread(timeout_ms=timeout_ms)
        else:
            super().shutdown(timeout_ms=timeout_ms)
        atexit.unregister(self._atexit)
        forksafe.unregister_before_fork(self.before_fork)
        forksafe.unregister_parent(self.after_fork_parent)
        forksafe.unregister(self.after_fork_child)


@cache
def get_native_runtime() -> NativeRuntime:
    """Return the process-wide NativeRuntime singleton, creating it on first use.

    The first call also registers an atexit hook to shut the runtime down.
    """
    return NativeRuntime()
