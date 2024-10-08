"""
An API to provide fork-safe functions.
"""
import functools
import logging
import os
import threading
import typing
import weakref

import wrapt


log = logging.getLogger(__name__)


_registry = []  # type: typing.List[typing.Callable[[], None]]
_registry_before_fork = []  # type: typing.List[typing.Callable[[], None]]

# Some integrations might require after-fork hooks to be executed after the
# actual call to os.fork with earlier versions of Python (<= 3.6), else issues
# like SIGSEGV will occur. Setting this to True will cause the after-fork hooks
# to be executed after the actual fork, which seems to prevent the issue.
_soft = True


# Flag to determine, from the parent process, if fork has been called
_forked = False


def set_forked():
    global _forked

    _forked = True


def has_forked():
    return _forked


def run_hooks(registry):
    # type: (typing.List[typing.Callable[[], None]]) -> None
    for hook in list(registry):
        try:
            hook()
        except Exception:
            # Mimic the behaviour of Python's fork hooks.
            log.exception("Exception ignored in forksafe hook %r", hook)


ddtrace_before_fork = functools.partial(run_hooks, _registry_before_fork)
ddtrace_after_in_child = functools.partial(run_hooks, _registry)


def register_hook(registry, hook):
    registry.append(hook)
    return hook


register_before_fork = functools.partial(register_hook, _registry_before_fork)
register = functools.partial(register_hook, _registry)


def unregister(after_in_child):
    # type: (typing.Callable[[], None]) -> None
    try:
        _registry.remove(after_in_child)
    except ValueError:
        log.info("after_in_child hook %s was unregistered without first being registered", after_in_child.__name__)


def unregister_before_fork(before_fork):
    # type: (typing.Callable[[], None]) -> None
    try:
        _registry_before_fork.remove(before_fork)
    except ValueError:
        log.info("before_in_child hook %s was unregistered without first being registered", before_fork.__name__)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(before=ddtrace_before_fork, after_in_child=ddtrace_after_in_child, after_in_parent=set_forked)

_resetable_objects = weakref.WeakSet()  # type: weakref.WeakSet[ResetObject]


def _reset_objects():
    # type: (...) -> None
    for obj in list(_resetable_objects):
        try:
            obj._reset_object()
        except Exception:
            log.exception("Exception ignored in object reset forksafe hook %r", obj)


register(_reset_objects)


_T = typing.TypeVar("_T")


class ResetObject(wrapt.ObjectProxy, typing.Generic[_T]):
    """An object wrapper object that is fork-safe and resets itself after a fork.

    When a Python process forks, a Lock can be in any state, locked or not, by any thread. Since after fork all threads
    are gone, Lock objects needs to be reset. CPython does this with an internal `threading._after_fork` function. We
    use the same mechanism here.

    """

    def __init__(
        self, wrapped_class  # type: typing.Type[_T]
    ):
        # type: (...) -> None
        super(ResetObject, self).__init__(wrapped_class())
        self._self_wrapped_class = wrapped_class
        _resetable_objects.add(self)

    def _reset_object(self):
        # type: (...) -> None
        self.__wrapped__ = self._self_wrapped_class()


def Lock():
    # type: (...) -> ResetObject[threading.Lock]
    return ResetObject(threading.Lock)


def RLock():
    # type: (...) -> ResetObject[threading.RLock]
    return ResetObject(threading.RLock)


def Event():
    # type: (...) -> ResetObject[threading.Event]
    return ResetObject(threading.Event)
