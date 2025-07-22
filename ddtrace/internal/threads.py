import atexit

from ddtrace.internal import forksafe
from ddtrace.internal._threads import Lock
from ddtrace.internal._threads import PeriodicThread
from ddtrace.internal._threads import RLock
from ddtrace.internal._threads import begin_reset_locks
from ddtrace.internal._threads import end_reset_locks
from ddtrace.internal._threads import periodic_threads
from ddtrace.internal._threads import reset_locks


__all__ = [
    "Lock",
    "PeriodicThread",
    "RLock",
]


@atexit.register
def _():
    # If the interpreter is shutting down we need to make sure that the threads
    # are stopped before the runtime is marked as finalising. This is because
    # any attempt to acquire the GIL while the runtime is finalising will cause
    # the acquiring thread to be terminated with pthread_exit (on Linux). This
    # causes a SIGABRT with GCC that cannot be caught, so we need to avoid
    # getting to that stage.
    for thread in periodic_threads.values():
        thread._atexit()


@forksafe.register
def _() -> None:
    # No threads are running after a fork so we clean up the periodic threads
    for thread in periodic_threads.values():
        thread._after_fork()
    periodic_threads.clear()


forksafe.register_before_fork(begin_reset_locks)
forksafe.register(reset_locks)
forksafe.register_after_parent(end_reset_locks)
