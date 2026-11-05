from functools import cache
from functools import wraps
import logging
import sys
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.module import ModuleWatchdog
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
        self._shut_down = False
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
        self._shut_down = True
        self._uninstall_subprocess_fork_hook()
        atexit.unregister(self._atexit)


_runtime: Optional[NativeRuntime] = None


@cache
def get_native_runtime() -> NativeRuntime:
    """Return the process-wide NativeRuntime singleton, creating it on first use.

    The first call also registers an atexit hook to shut the runtime down.
    """
    global _runtime

    _runtime = NativeRuntime()
    return _runtime


def _rebuild_after_fork() -> None:
    # Rebuild the runtime in the child before any other child hook can use it,
    # rather than lazily on whichever thread uses it first.
    runtime = _runtime
    if runtime is not None and not runtime._shut_down:
        runtime.rebuild_after_fork()


forksafe.register_before_child_hooks(_rebuild_after_fork)


def _wrap_close_open_fds(close_open_fds: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(close_open_fds, "__ddtrace_native_runtime__", False):
        return close_open_fds

    @wraps(close_open_fds)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        runtime = _runtime
        if runtime is None or runtime._shut_down:
            return close_open_fds(*args, **kwargs)

        # Celery closes every inherited file descriptor with this function, e.g.
        # in the embedded beat process, which keeps using the runtime. Stop
        # the runtime while its descriptors are still valid, then rebuild it
        # and reset its workers as in a fork child, so that no worker keeps a
        # handle on a closed descriptor.
        runtime.before_fork()
        try:
            return close_open_fds(*args, **kwargs)
        finally:
            runtime.after_fork_child()

    setattr(wrapper, "__ddtrace_native_runtime__", True)
    return wrapper


@ModuleWatchdog.after_module_imported("celery.platforms")
def _(platforms: Any) -> None:
    # Every Celery call site goes through celery.platforms.close_open_fds:
    # beat and the worker reload by attribute, DaemonContext by module global.
    # Up to Celery 5.6 it is billiard's function; from 5.7 Celery closes the
    # descriptors listed in /proc/self/fd itself and only falls back to
    # billiard's, so wrapping billiard would not cover it.
    platforms.close_open_fds = _wrap_close_open_fds(platforms.close_open_fds)
