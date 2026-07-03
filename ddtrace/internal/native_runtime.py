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

    # Fork-hook diagnostics (APMSP fork investigation).
    #
    # ``before_fork`` tears the tokio runtime down in the parent so the child
    # inherits an empty slot. If it is skipped for a given fork, the child
    # inherits a *live* runtime and the native ``after_fork_child`` has to
    # abandon it (``mem::forget``). These process-wide counters let staging
    # tell us, with hard numbers, how often ``before_fork`` is skipped for a
    # fork that still runs ``after_fork_child`` (the asymmetric case).
    before_fork_calls: int = 0
    after_fork_parent_calls: int = 0
    after_fork_child_calls: int = 0
    # Number of forks where after_fork_child ran without a preceding before_fork.
    before_fork_skipped: int = 0

    def __init__(self) -> None:
        super().__init__()
        # Set by before_fork, cleared after each fork in both parent and child.
        # Lets after_fork_child detect that no before_fork ran for this fork.
        self._before_fork_ran = False
        forksafe.register_before_fork(self.before_fork)
        forksafe.register_after_parent(self.after_fork_parent)
        forksafe.register(self.after_fork_child)
        atexit.register(self._atexit)

    def before_fork(self) -> None:
        NativeRuntime.before_fork_calls += 1
        self._before_fork_ran = True
        super().before_fork()

    def after_fork_parent(self) -> None:
        NativeRuntime.after_fork_parent_calls += 1
        self._before_fork_ran = False
        super().after_fork_parent()

    def after_fork_child(self) -> None:
        NativeRuntime.after_fork_child_calls += 1
        skipped = not self._before_fork_ran
        self._before_fork_ran = False
        try:
            super().after_fork_child()
        finally:
            if skipped:
                NativeRuntime.before_fork_skipped += 1
                # Ground-truth from the native side: how many times a stale
                # inherited runtime actually had to be forgotten. Available only
                # once the instrumented native extension is built; guard for
                # older builds.
                try:
                    native_forgotten = self.stale_runtimes_forgotten()  # type: ignore[attr-defined]
                except Exception:
                    native_forgotten = -1
                log.warning(
                    "native runtime: before_fork was skipped for this fork "
                    "(py_skipped=%d native_forgotten=%s after_child_calls=%d before_calls=%d); "
                    "child inherited a live tokio runtime and had to abandon it",
                    NativeRuntime.before_fork_skipped,
                    native_forgotten,
                    NativeRuntime.after_fork_child_calls,
                    NativeRuntime.before_fork_calls,
                )

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
