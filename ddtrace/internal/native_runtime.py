from functools import cache
import logging
import sys
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.native import SharedRuntime


log = logging.getLogger(__name__)

_DEFAULT_SHUTDOWN_TIMEOUT_MS = 3000


def _is_darwin() -> bool:
    # A direct sys.platform comparison is narrowed to the machine running mypy,
    # so the other branch is reported unreachable.
    return sys.platform == "darwin"


class NativeRuntime(SharedRuntime):
    """Manages a SharedRuntime with native fork-safe lifecycle hooks.

    The SharedRuntime wraps a Tokio async runtime shared across TraceExporter
    instances. Native pthread_atfork hooks ensure the runtime is correctly
    paused and resumed even when a native caller bypasses Python's fork hooks.
    Python-level before_fork / after_fork_parent hooks also run so this instance
    can track `_paused` for callers on the Python side. The child side only
    tracks the flag; see _resume_after_fork_child.
    """

    def __init__(self) -> None:
        super().__init__()
        # True from before_fork until whichever of after_fork_parent/after_fork_child runs. No
        # runtime exists in that window, so a blocking flush issued from a fork hook (this one's
        # own, or another product's, since forksafe dispatches every registered hook in order) would
        # wait on a condvar nothing can ever notify and hang os.fork() forever. Anything that wants
        # to flush should check this first and fall back to a fire-and-forget flush instead.
        self._paused = False
        self.register_at_fork()
        forksafe.register_before_fork(self.before_fork)
        forksafe.register_after_parent(self.after_fork_parent)
        forksafe.register(self._resume_after_fork_child)
        forksafe.register_before_child_hooks(self.defer_after_fork_child)
        forksafe.register_after_child_hooks(self.allow_after_fork_child)
        self._original_fork_exec: Optional[Callable[..., Any]] = None
        self._wrapped_fork_exec: Optional[Callable[..., Any]] = None
        self._install_subprocess_fork_hook()
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

    def _resume_after_fork_child(self) -> None:
        # Deliberately does not call the native after_fork_child(). Rebuilding the runtime from the
        # child fork hook unparks the inherited Tokio I/O driver, which panics once a process manager
        # such as Celery beat closes the descriptors the child inherited. It also stamps the current
        # pid, which stops allow_after_fork_child from arming the lazy restart that would otherwise
        # abandon the inherited runtime on first use.
        self._paused = False

    def _install_subprocess_fork_hook(self) -> None:
        # subprocess and asyncio call _posixsubprocess.fork_exec directly, so
        # os.register_at_fork never runs for them. On macOS, libSystem locks the
        # resolver before our pthread_atfork handler. That handler pauses runtime
        # workers, and a worker inside getaddrinfo needs the same lock, so the
        # pause deadlocks. Mark this thread first so the handler skips the pause.
        if not _is_darwin():
            return

        import _posixsubprocess

        original_fork_exec = _posixsubprocess.fork_exec
        if getattr(original_fork_exec, "__ddtrace_native_fork_hook__", False):
            return

        def fork_exec(*args: Any, **kwargs: Any) -> Any:
            self.before_python_fork()
            try:
                return original_fork_exec(*args, **kwargs)
            finally:
                self.after_python_fork_parent()

        setattr(fork_exec, "__ddtrace_native_fork_hook__", True)
        self._original_fork_exec = original_fork_exec
        self._wrapped_fork_exec = fork_exec
        setattr(_posixsubprocess, "fork_exec", fork_exec)
        # subprocess binds the C function into its own global at import time.
        self._rebind_imported_subprocess_fork_exec(original_fork_exec, fork_exec)

    def _uninstall_subprocess_fork_hook(self) -> None:
        wrapped_fork_exec = self._wrapped_fork_exec
        if wrapped_fork_exec is None:
            return

        import _posixsubprocess

        if _posixsubprocess.fork_exec is wrapped_fork_exec:
            setattr(_posixsubprocess, "fork_exec", self._original_fork_exec)
        self._rebind_imported_subprocess_fork_exec(wrapped_fork_exec, self._original_fork_exec)
        self._original_fork_exec = None
        self._wrapped_fork_exec = None

    @staticmethod
    def _rebind_imported_subprocess_fork_exec(current: Any, replacement: Any) -> None:
        subprocess_module = sys.modules.get("subprocess")
        if subprocess_module is not None and getattr(subprocess_module, "_fork_exec", None) is current:
            setattr(subprocess_module, "_fork_exec", replacement)

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
        self._uninstall_subprocess_fork_hook()
        atexit.unregister(self._atexit)
        forksafe.unregister_before_fork(self.before_fork)
        forksafe.unregister_parent(self.after_fork_parent)
        forksafe.unregister(self._resume_after_fork_child)
        forksafe.unregister_before_child_hooks(self.defer_after_fork_child)
        forksafe.unregister_after_child_hooks(self.allow_after_fork_child)


@cache
def get_native_runtime() -> NativeRuntime:
    """Return the process-wide NativeRuntime singleton, creating it on first use.

    The first call also registers an atexit hook to shut the runtime down.
    """
    return NativeRuntime()
