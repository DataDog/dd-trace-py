import logging
from typing import Optional

from ddtrace.internal import forksafe
import ddtrace.internal.native as native


log = logging.getLogger(__name__)


class NativeRuntime:
    """Manages a SharedRuntime with fork-safe lifecycle hooks.

    The SharedRuntime wraps a Tokio async runtime shared across TraceExporter
    instances. This class registers before_fork / after_fork_parent /
    after_fork_child hooks so the runtime is correctly paused and resumed
    around process forks.
    """

    def __init__(self) -> None:
        self._shared_runtime = native.SharedRuntime()
        forksafe.register_before_fork(self._before_fork)
        forksafe.register_after_parent(self._after_fork_parent)
        forksafe.register(self._after_fork_child)

    @property
    def shared_runtime(self) -> native.SharedRuntime:
        return self._shared_runtime

    def _before_fork(self) -> None:
        self._shared_runtime.before_fork()

    def _after_fork_parent(self) -> None:
        self._shared_runtime.after_fork_parent()

    def _after_fork_child(self) -> None:
        self._shared_runtime.after_fork_child()

    def shutdown(self, timeout_ms: Optional[int] = None) -> None:
        """Shut down the shared Tokio runtime.

        Args:
            timeout_ms: Maximum time in milliseconds to wait for shutdown.
                If None, waits indefinitely — only safe if all workers have
                already been stopped (e.g. via TraceExporter.shutdown).
        """
        self._shared_runtime.shutdown(timeout_ms=timeout_ms)
        forksafe.unregister_before_fork(self._before_fork)
        forksafe.unregister_parent(self._after_fork_parent)
        forksafe.unregister(self._after_fork_child)

    def __del__(self) -> None:
        forksafe.unregister_before_fork(self._before_fork)
        forksafe.unregister_parent(self._after_fork_parent)
        forksafe.unregister(self._after_fork_child)
