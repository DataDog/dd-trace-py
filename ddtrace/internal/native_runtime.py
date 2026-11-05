from functools import cache
from functools import wraps
import logging
import os
import sys
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal import atexit
from ddtrace.internal import forksafe
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.native import SharedRuntime
from ddtrace.internal.threads import original_local


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

# Called after the runtime is replaced outside a fork, so that the owners of
# workers on the old runtime rebuild them, as they do in a fork child.
_replacement_hooks: list[Callable[[], None]] = []


def on_runtime_replaced(hook: Callable[[], None]) -> Callable[[], None]:
    """Register a hook to run after the runtime is replaced, e.g. by a descriptor cleanup."""
    _replacement_hooks.append(hook)
    return hook


def unregister_on_runtime_replaced(hook: Callable[[], None]) -> None:
    try:
        _replacement_hooks.remove(hook)
    except ValueError:
        pass


def _replace_runtime(runtime: NativeRuntime) -> None:
    runtime.replace_runtime()
    for hook in list(_replacement_hooks):
        try:
            hook()
        except Exception:
            log.debug("Native runtime replacement hook %r failed", hook, exc_info=True)


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


# Wrappers can nest, e.g. a wrapped cleanup function calling os.closerange:
# only the outermost one stops and rebuilds the runtime, otherwise the outer
# cleanup would close the descriptors of a runtime rebuilt by the inner one.
_fd_cleanup = original_local()


def _stop_while_closing_fds(close: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a function that closes inherited file descriptors.

    The runtime's own descriptors are among those closed. Stop the runtime while
    they are still valid, then replace it, as in a fork child: the old runtime
    and its workers, whose connections may refer to closed descriptors, are
    never touched again, and their owners rebuild the workers.
    """
    if getattr(close, "__ddtrace_native_runtime__", False):
        return close

    @wraps(close)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        runtime = _runtime
        depth = getattr(_fd_cleanup, "depth", 0)
        if runtime is None or runtime._shut_down or depth:
            return close(*args, **kwargs)

        runtime.before_fork()
        _fd_cleanup.depth = depth + 1
        try:
            return close(*args, **kwargs)
        finally:
            _fd_cleanup.depth = depth
            _replace_runtime(runtime)

    setattr(wrapper, "__ddtrace_native_runtime__", True)
    return wrapper


def _stop_before_exec(execute: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an exec function so the process image is replaced with the runtime stopped."""
    if getattr(execute, "__ddtrace_native_runtime__", False):
        return execute

    @wraps(execute)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        runtime = _runtime
        if runtime is None or runtime._shut_down:
            return execute(*args, **kwargs)

        runtime.before_fork()
        try:
            return execute(*args, **kwargs)
        except BaseException:
            # The exec failed and this process carries on: resume the runtime
            # with its workers' state intact, as in a fork parent.
            runtime.after_fork_parent()
            raise

    setattr(wrapper, "__ddtrace_native_runtime__", True)
    return wrapper


# Daemonizing libraries such as python-daemon fork, close every inherited
# descriptor with os.closerange and keep running. Every os.exec* function goes
# through os.execv or os.execve, which os looks up by name on each call.
setattr(os, "closerange", _stop_while_closing_fds(os.closerange))
setattr(os, "execv", _stop_before_exec(os.execv))
setattr(os, "execve", _stop_before_exec(os.execve))


@ModuleWatchdog.after_module_imported("celery.platforms")
def _(platforms: Any) -> None:
    # Every Celery call site goes through celery.platforms.close_open_fds:
    # beat and the worker reload by attribute, DaemonContext by module global.
    # Up to Celery 5.6 it is billiard's function; from 5.7 Celery closes the
    # descriptors listed in /proc/self/fd itself and only falls back to
    # billiard's, so wrapping billiard would not cover it.
    platforms.close_open_fds = _stop_while_closing_fds(platforms.close_open_fds)
