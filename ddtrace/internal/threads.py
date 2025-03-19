import atexit

from ddtrace.internal import forksafe
from ddtrace.internal._threads import Lock as NativeLock
from ddtrace.internal._threads import PeriodicThread
from ddtrace.internal._threads import periodic_threads


__all__ = [
    "PeriodicThread",
    "periodic_threads",
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


class _BaseLock(NativeLock):
    __reentrant__: bool

    def __init__(self):
        return super().__init__(reentrant=self.__reentrant__)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False


class _Lock(_BaseLock):
    __reentrant__ = False


class _RLock(_BaseLock):
    __reentrant__ = True


def Lock() -> forksafe.ResetObject[_Lock]:
    return forksafe.ResetObject(_Lock)


def RLock() -> forksafe.ResetObject[_RLock]:
    return forksafe.ResetObject(_RLock)
