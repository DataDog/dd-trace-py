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
    """

    def __init__(self) -> None:
        super().__init__()
        self.register_at_fork()
        forksafe.register_before_child_hooks(self.defer_after_fork_child)
        forksafe.register_after_child_hooks(self.allow_after_fork_child)
        self._original_fork_exec: Optional[Callable[..., Any]] = None
        self._wrapped_fork_exec: Optional[Callable[..., Any]] = None
        self._install_subprocess_fork_hook()
        atexit.register(self._atexit)
        atexit.register_on_exit_signal(self._atexit)

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
        forksafe.unregister_before_child_hooks(self.defer_after_fork_child)
        forksafe.unregister_after_child_hooks(self.allow_after_fork_child)


@cache
def get_native_runtime() -> NativeRuntime:
    """Return the process-wide NativeRuntime singleton, creating it on first use.

    The first call also registers an atexit hook to shut the runtime down.
    """
    return NativeRuntime()
