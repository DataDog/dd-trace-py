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

# AIDEV-NOTE: Exact text of the RuntimeErrors ddtrace/internal/_threads.cpp raises for a
# thread/periodic-thread that was never started (via PyErr_SetString(PyExc_RuntimeError, ...)).
# Callers such as ddtrace.profiling.scheduler.Scheduler._rollback_start match on these strings to
# tell "never started" apart from a real failure; keep all three in sync if the native messages
# change.
THREAD_NOT_STARTED_ERROR = "Thread not started"
PERIODIC_THREAD_NOT_STARTED_ERROR = "Periodic thread not started"


# Forking state management. This is a barrier to either prevent new threads
# from being started while forking, or to allow a thread to be started
# completely if a fork comes in the middle of it.
_forking = False
_forking_lock = forksafe.Lock()


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
            self._restart_cancelled = False
            # We cannot start a new thread while we are forking, because we are
            # trying to stop them all. In that case, we take note of the thread
            # and start it after the fork.
            if not _forking:
                super().start()
            else:
                _threads_to_start_after_fork.append(t.cast(BoundMethod, super().start))

    def _cancel_deferred_start(self) -> bool:
        with _forking_lock:
            return self._cancel_deferred_start_unlocked()

    def _cancel_deferred_start_unlocked(self) -> bool:
        # AIDEV-NOTE: Callers outside this module (ddtrace.profiling.scheduler.Scheduler
        # ._rollback_start) call this `_unlocked` variant directly while holding
        # `_forking_lock` themselves, instead of `_cancel_deferred_start()`, because they need
        # the decision made here (deferred vs. already-running) to stay atomic with a fallback
        # stop/join they run under the same lock. If this method's contract (return value
        # semantics, or what state it mutates) changes, update that call site too.
        retained_starts = [start for start in _threads_to_start_after_fork if start.__self__ is not self]
        start_was_deferred = len(retained_starts) != len(_threads_to_start_after_fork)
        _threads_to_start_after_fork[:] = retained_starts

        # AIDEV-NOTE: A running worker still needs pre-fork stop/join, but its owner can
        # prevent the completed fork protocol from restarting it.
        self._restart_cancelled = True
        _threads_to_restart_after_fork.discard(self)
        return start_was_deferred


# Set of running periodic threads that need to be restarted after a fork.
_threads_to_restart_after_fork: set[_PeriodicThread] = set()


# A typical scenario is that of forking worker threads in a loop. For the
# parent process, this would mean having to stop and restart the threads in
# between forks, which is not ideal. Instead, we can use a timer to restart
# the threads after a certain amount of time has passed since the last fork.
# This way, we can avoid stopping and restarting the threads in between forks.
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
                for thread in _threads_to_restart_after_fork.copy():
                    if isinstance(thread, ThreadRestartTimer):
                        # Skip any ThreadRestartTimer instance,
                        # to avoid restarting orphaned timer instances that were
                        # caught in periodic_threads during a fork.
                        continue
                    log.debug("Restarting thread %s after fork", thread.name)
                    try:
                        thread._after_fork(force=True)
                    except Exception as e:
                        log.error("failed to restart periodic thread %s after fork: %s", thread.name, e)
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

    with _forking_lock:
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

    if _threads_to_restart_after_fork or _threads_to_start_after_fork:
        ThreadRestartTimer.set()


@forksafe.register_before_fork
def _before_fork() -> None:
    global _threads_to_restart_after_fork, _forking_lock, _forking

    ThreadRestartTimer.touch()

    with _forking_lock:
        _forking = True

        # AIDEV-NOTE: Native starts and restarts finish registration before the wrapper releases
        # _forking_lock, so this snapshot includes every worker that can be running at fork time.
        threads_to_stop = set(periodic_threads.values())
        threads_to_stop.update(_threads_to_restart_after_fork)

        _threads_to_restart_after_fork.update(
            thread for thread in threads_to_stop if not getattr(thread, "_restart_cancelled", False)
        )
        threads_to_stop_snapshot = tuple(threads_to_stop)

    # Stop all the periodic threads that are still running, without executing
    # the shutdown methods, if any. This ensures that we can stop the threads
    # more promptly.
    for thread in threads_to_stop_snapshot:
        log.debug("Stopping thread %s before fork", thread.name)
        thread._before_fork()

    # Join all the threads to ensure they are stopped before the fork.
    for thread in threads_to_stop_snapshot:
        log.debug("Joining thread %s before fork", thread.name)
        thread.join()
