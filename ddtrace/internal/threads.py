from time import monotonic_ns
import typing as t

from ddtrace.internal import forksafe
from ddtrace.internal._threads import PeriodicThread as _PeriodicThread
from ddtrace.internal._threads import periodic_threads
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# We try to import the stdlib locks from the _thread module, where they are
# implemented in C for CPython for most platforms. If that fails, we fall back
# to the threading module, which provides a pure Python implementation that
# should work on all platforms. We also make sure to grab a reference to the
# original lock classes, in case they get patched by monkey-patching libraries
# like gevent.
try:
    from _thread import allocate_lock as Lock
except ImportError:
    from threading import Lock

try:
    from _thread import RLock
except ImportError:
    from threading import RLock


__all__ = [
    "Lock",
    "PeriodicThread",
    "RLock",
]


# Forking state management. This is a barrier to either prevent new threads
# from being started while forking, or to allow a thread to be started
# completely if a fork comes in the middle of it.
_forking = False
_forking_lock = Lock()


class BoundMethod(t.Protocol):
    __self__: t.Any

    def __call__(self) -> None: ...


# List of threads that have requested to be started while forking. These will
# be started after the fork is complete.
_threads_to_start_after_fork: list[BoundMethod] = []


def _safe_restart(start: t.Callable[[], None], name: t.Optional[str] = None) -> None:
    """Invoke a post-fork thread-start callable, logging resource errors instead of raising.

    The native layer translates pthread_create failures (EAGAIN, ENOMEM) into
    OSError. Post-fork restart is triggered automatically by forksafe hooks —
    there is no explicit caller that can handle the error, so losing a
    periodic thread to resource exhaustion must not crash the host.
    Explicit start() calls let OSError propagate so the caller can react.
    """
    try:
        start()
    except Exception as e:
        log.error("failed to start periodic thread %s: %s", name, e)


class PeriodicThread(_PeriodicThread):
    """A fork-safe periodic thread."""

    __autorestart__ = True

    def start(self) -> None:
        with _forking_lock:
            # We cannot start a new thread while we are forking, because we are
            # trying to stop them all. In that case, we take note of the thread
            # and start it after the fork.
            if not _forking:
                super().start()
            else:
                _threads_to_start_after_fork.append(t.cast(BoundMethod, super().start))


# Set of running periodic threads that need to be restarted after a fork.
_threads_to_restart_after_fork: set[_PeriodicThread] = set()


# A typical scenario is that of forking worker threads in a loop. Starts
# requested during fork are delayed until the process has stopped forking for a
# moment. Threads that were already running before fork are restarted
# immediately in the parent; otherwise periodic flushers can stay stopped for an
# entire fork storm and accumulate unbounded in-memory work.
class ThreadRestartTimer(PeriodicThread):
    __timeout__ = int(1e8)  # nanoseconds

    _instance: t.Optional["ThreadRestartTimer"] = None
    _timestamp = 0

    def __init__(self):
        super().__init__(self.__timeout__ / 1e9, self._restart_threads, name=f"{__name__}:{self.__class__.__name__}")

    def _restart_threads(self) -> None:
        # Restart the threads after we have stopped calling fork for a while.
        with _forking_lock:
            # If we are forking, we will try again later.
            if _forking:
                return

            # If we haven't have calls to fork for a while, we can restart the
            # threads. This way we avoid stopping and restarting the threads
            # in between forks.
            if monotonic_ns() >= self._timestamp:  # 100ms
                _threads_to_restart_after_fork.clear()

                for thread_start in _threads_to_start_after_fork:
                    log.debug("Starting thread %s after fork", thread_start.__self__.name)
                    _safe_restart(thread_start, thread_start.__self__.name)
                _threads_to_start_after_fork.clear()

                # We no longer need this thread so we clear it.
                self.clear()

    @classmethod
    def clear(cls):
        """Clear the timer and stop it if it is running."""
        if cls._instance is not None:
            cls._instance.stop()
            cls._instance = None

    @classmethod
    def touch(cls):
        """Set the new expiration time for the timer."""
        cls._timestamp = monotonic_ns() + cls.__timeout__

    @classmethod
    def set(cls):
        """Set the timer to restart the threads after a fork."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.start()
        else:
            # We have already created the timer, so we let the forksafe logic
            # handle the restart instead of creating a new instance.
            cls._instance._after_fork()


@forksafe.register
def _after_fork_child():
    global _forking

    _forking = False

    # Restart the threads immediately. It is unlikely that there will be another
    # call to fork here. _after_fork() (without force=True) respects
    # __autorestart__: cleanup always runs, but the thread is only restarted
    # when __autorestart__ is True. This is intentional in the child — threads
    # with __autorestart__ = False (e.g. RemoteConfigPoller) should not run in
    # forked workers.
    for thread in _threads_to_restart_after_fork.copy():
        log.debug("Restarting thread %s after fork in child", thread.name)
        try:
            thread._after_fork()
        except Exception as e:
            log.error("failed to restart periodic thread %s after fork in child: %s", thread.name, e)
    _threads_to_restart_after_fork.clear()

    for thread_start in _threads_to_start_after_fork.copy():
        log.debug("Starting thread %s after fork in child", thread_start.__self__.name)
        _safe_restart(thread_start, thread_start.__self__.name)
    _threads_to_start_after_fork.clear()


@forksafe.register_after_parent
def _after_fork_parent() -> None:
    global _forking

    _forking = False

    for thread in _threads_to_restart_after_fork.copy():
        log.debug("Restarting thread %s after fork in parent", thread.name)
        try:
            thread._after_fork(force=True)
        except Exception as e:
            log.error("failed to restart periodic thread %s after fork in parent: %s", thread.name, e)
    _threads_to_restart_after_fork.clear()

    if _threads_to_start_after_fork:
        ThreadRestartTimer.set()


@forksafe.register_before_fork
def _before_fork() -> None:
    global _threads_to_restart_after_fork, _forking_lock, _forking

    ThreadRestartTimer.touch()

    with _forking_lock:
        _forking = True

    # Take note of periodic threads that are currently running and will need to
    # be restarted. Do not include ThreadRestartTimer itself: it is an internal
    # debounce helper, not an application/service worker, and restarting stale
    # timer instances after a fork loop can leave extra periodic threads alive.
    running_threads = set(periodic_threads.values())
    _threads_to_restart_after_fork.update(
        thread for thread in running_threads if not isinstance(thread, ThreadRestartTimer)
    )

    # Stop all the periodic threads that are still running, without executing
    # the shutdown methods, if any. This ensures that we can stop the threads
    # more promptly.
    for thread in running_threads:
        log.debug("Stopping thread %s before fork", thread.name)
        thread._before_fork()

    # Join all the threads to ensure they are stopped before the fork.
    for thread in running_threads:
        log.debug("Joining thread %s before fork", thread.name)
        thread.join()
