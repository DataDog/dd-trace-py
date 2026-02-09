import atexit
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
_threads_to_start_after_fork: t.List[BoundMethod] = []


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
_threads_to_restart_after_fork: t.Set[_PeriodicThread] = set()


@atexit.register
def _():
    # If the interpreter is shutting down we need to make sure that the threads
    # are stopped before the runtime is marked as finalising. This is because
    # any attempt to acquire the GIL while the runtime is finalising will cause
    # the acquiring thread to be terminated with pthread_exit (on Linux). This
    # causes a SIGABRT with GCC that cannot be caught, so we need to avoid
    # getting to that stage.
    for thread in list(periodic_threads.values()):
        thread._atexit()


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
        super().__init__(self.__timeout__ / 1e9, self._restart_threads, name=f"{__name__}.{self.__class__.__name__}")

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
                for thread in _threads_to_restart_after_fork:
                    if thread is self:
                        # This has already been restarted by the after-fork hook.
                        continue
                    log.debug("Restarting thread %s after fork", thread.name)
                    thread._after_fork()
                _threads_to_restart_after_fork.clear()

                for thread_start in _threads_to_start_after_fork:
                    log.debug("Starting thread %s after fork", thread_start.__self__.name)
                    thread_start()
                _threads_to_start_after_fork.clear()

                # We no longer need this thread so we stop it and clear the
                # instance.
                self.stop()

                self._instance = None

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
    # call to fork here.
    for thread in _threads_to_restart_after_fork:
        if isinstance(thread, PeriodicThread) and not thread.__autorestart__:
            continue
        log.debug("Restarting thread %s after fork in child", thread.name)
        thread._after_fork()
    _threads_to_restart_after_fork.clear()

    for thread_start in _threads_to_start_after_fork:
        log.debug("Starting thread %s after fork in child", thread_start.__self__.name)
        thread_start()
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

    # Take note of all the periodic threads that are running and will need to be
    # restarted.
    _threads_to_restart_after_fork.update(periodic_threads.values())

    # Stop all the periodic threads that are still running, without executing
    # the shutdown methods, if any. This ensures that we can stop the threads
    # more promptly.
    for thread in _threads_to_restart_after_fork:
        log.debug("Stopping thread %s before fork", thread.name)
        thread._before_fork()

    # Join all the threads to ensure they are stopped before the fork.
    for thread in _threads_to_restart_after_fork:
        log.debug("Joining thread %s before fork", thread.name)
        thread.join()
