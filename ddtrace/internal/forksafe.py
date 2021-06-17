"""
An API to provide fork-safe functions.
"""
import logging
import os
import threading
import typing
import weakref

from ddtrace.vendor import wrapt


log = logging.getLogger(__name__)


_registry = []  # type: typing.List[typing.Callable[[], None]]


def ddtrace_after_in_child():
    # type: () -> None
    global _registry

    # DEV: we make a copy of the registry to prevent hook execution from
    # introducing new hooks, potentially causing an infinite loop.
    for hook in list(_registry):
        try:
            hook()
        except Exception:
            # Mimic the behaviour of Python's fork hooks.
            log.exception("Exception ignored in forksafe hook %r", hook)


def register(after_in_child):
    # type: (typing.Callable[[], None]) -> typing.Callable[[], None]
    """Register a function to be called after fork in the child process."""
    _registry.append(after_in_child)
    return after_in_child


def unregister(after_in_child):
    # type: (typing.Callable[[], None]) -> None
    """Unregister a function to be called after fork in the child process.

    Raises `ValueError` if the function was not registered.
    """
    _registry.remove(after_in_child)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=ddtrace_after_in_child)
else:
    _threading_after_fork = threading._after_fork  # type: ignore[attr-defined]

    def _after_fork():
        # type: () -> None
        _threading_after_fork()
        ddtrace_after_in_child()

    threading._after_fork = _after_fork  # type: ignore[attr-defined]


_resetable_objects = weakref.WeakSet()  # type: weakref.WeakSet[ResetObject]


def _reset_objects():
    # type: (...) -> None
    for obj in _resetable_objects:
        try:
            obj._reset_object()
        except Exception:
            log.exception("Exception ignored in object reset forksafe hook %r", obj)


register(_reset_objects)


class ResetObject(wrapt.ObjectProxy):
    """An object wrapper object that is fork-safe and resets itself after a fork.

    When a Python process forks, a Lock can be in any state, locked or not, by any thread. Since after fork all threads
    are gone, Lock objects needs to be reset. CPython does this with an internal `threading._after_fork` function. We
    use the same mechanism here.

    """

    def __init__(
        self, wrapped_class  # type: typing.Any
    ):
        # type: (...) -> None
        super(ResetObject, self).__init__(wrapped_class())
        self._self_wrapped_class = wrapped_class
        _resetable_objects.add(self)

    def _reset_object(self):
        # type: (...) -> None
        self.__wrapped__ = self._self_wrapped_class()


def Lock():
    # type: (...) -> ResetObject
    return ResetObject(threading.Lock)


def RLock():
    # type: (...) -> ResetObject
    return ResetObject(threading.RLock)


def Event():
    # type: (...) -> ResetObject
    return ResetObject(threading.Event)
