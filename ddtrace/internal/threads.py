import atexit

from ddtrace.internal import forksafe
from ddtrace.internal._threads import Lock
from ddtrace.internal._threads import PeriodicThread
from ddtrace.internal._threads import RLock
from ddtrace.internal._threads import acquire_all
from ddtrace.internal._threads import periodic_threads
from ddtrace.internal._threads import release_all


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


# Acquire all locks before a fork in the thread that is forking. We then
# release them after the fork in both the parent and child processes.
forksafe.register_before_fork(acquire_all)
forksafe.register(release_all)
forksafe.register_after_parent(release_all)
