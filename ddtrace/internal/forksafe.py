"""
An API to provide fork-safe functions.
"""

import functools
import logging
import os
import typing
import weakref

import wrapt

from ddtrace.internal import _unpatched
from ddtrace.internal.locks import RLock as _RLock
from ddtrace.internal.native.exceptions import is_panic_exception


log = logging.getLogger(__name__)

# IMPORTANT: Do not change typing.List to list until minimum Python version is 3.11+
# Module-level list[...] in Python 3.10 affects import timing. See packages.py for details.
_registry: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
_registry_before_fork: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
_registry_after_parent: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
_registry_before_child_hooks: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
_registry_after_child_hooks: typing.List[typing.Callable[[], None]] = []  # noqa: UP006

# Some integrations might require after-fork hooks to be executed after the
# actual call to os.fork with earlier versions of Python (<= 3.6), else issues
# like SIGSEGV will occur. Setting this to True will cause the after-fork hooks
# to be executed after the actual fork, which seems to prevent the issue.
_soft = True


# Flag to determine, from the parent process, if fork has been called
_forked = False

# Fork generation counter. This is incremented every time a fork occurs, and is
# used to determine if the current process is a child of a fork, and if so, how
# many generations of forks have occurred since the original process.
_fork_generation = 0

# Process managers such as Celery can close inherited file descriptors after
# Python's after-in-child callbacks return. Native workers must not be rebuilt while this
# flag is set because their newly opened descriptors would be swept up by that later cleanup.
_in_child_hook = False


def set_forked():
    global _forked

    _forked = True


def has_forked() -> bool:
    return _forked


def set_fork_child() -> None:
    global _fork_generation

    _fork_generation += 1


def is_fork_child() -> bool:
    return _fork_generation > 0


def get_generation() -> int:
    return _fork_generation


def in_child_hook() -> bool:
    return _in_child_hook


def run_hooks(registry: list[typing.Callable[[], None]]) -> None:
    for hook in list(registry):
        try:
            hook()
        except Exception:
            # Mimic the behaviour of Python's fork hooks.
            log.exception("Exception ignored in forksafe hook %r", hook)
        except BaseException as e:
            # A native hook can raise pyo3_runtime.PanicException. We handle it
            # to allow other hooks to continue in this expected case.
            if not is_panic_exception(e):
                raise
            log.exception("Exception ignored in forksafe hook %r", hook)


# CPython runs the before-fork and after-fork-in-parent callbacks outside of
# any lock, so several threads can be forking at the same time. Our hooks
# assume that before-fork, fork and after-fork happen as one uninterrupted
# sequence, so we serialize them: the thread that runs the before-fork hooks
# holds this lock until its after-fork hooks have completed. Only the forking
# thread survives in the child, so threads queued on the lock exist only in
# the parent, where they take their turn once the lock is released. Threads
# started by ddtrace never fork and never take this lock, which is what makes
# it safe for the before-fork hooks to join them while holding it.
#
# This is an RLock because it records its owner as part of acquire() and
# release(). Tracking the owner separately would leave windows where an
# asynchronous exception (e.g. KeyboardInterrupt) leaves the lock held with no
# recorded owner, which would block every subsequent fork.
_fork_lock = _RLock()


def ddtrace_before_fork() -> None:
    _fork_lock.acquire()

    run_hooks(_registry_before_fork)


def ddtrace_after_in_parent() -> None:
    try:
        run_hooks(_registry_after_parent)
    finally:
        try:
            _fork_lock.release()
        except RuntimeError:
            # This thread does not hold the lock. An exception (e.g.
            # KeyboardInterrupt) can interrupt the blocking acquire in
            # ddtrace_before_fork, and CPython reports it and forks anyway.
            pass


def run_child_hooks() -> None:
    global _in_child_hook, _fork_lock

    # The child is single-threaded, so a fresh lock is equivalent to releasing
    # the inherited one. Unlike a release, it also covers callers that run the
    # child hooks without a matching before-fork call (e.g. uWSGI workers
    # forked by the master at the C level).
    _fork_lock = _RLock()

    _in_child_hook = True
    run_hooks(_registry_before_child_hooks)
    try:
        run_hooks(_registry)
    finally:
        run_hooks(_registry_after_child_hooks)
        _in_child_hook = False


ddtrace_after_in_child = run_child_hooks


def register_hook(registry, hook):
    registry.append(hook)
    return hook


register_before_fork = functools.partial(register_hook, _registry_before_fork)
register = functools.partial(register_hook, _registry)
register_after_parent = functools.partial(register_hook, _registry_after_parent)
register_before_child_hooks = functools.partial(register_hook, _registry_before_child_hooks)
register_after_child_hooks = functools.partial(register_hook, _registry_after_child_hooks)

register(set_fork_child)
register_after_parent(set_forked)


def unregister(after_in_child: typing.Callable[[], None]) -> None:
    try:
        _registry.remove(after_in_child)
    except ValueError:
        log.info("after_in_child hook %r was unregistered without first being registered", after_in_child)


def unregister_parent(after_in_parent: typing.Callable[[], None]) -> None:
    try:
        _registry_after_parent.remove(after_in_parent)
    except ValueError:
        log.info("after_in_parent hook %r was unregistered without first being registered", after_in_parent)


def unregister_before_fork(before_fork: typing.Callable[[], None]) -> None:
    try:
        _registry_before_fork.remove(before_fork)
    except ValueError:
        log.info("before_in_child hook %r was unregistered without first being registered", before_fork)


def unregister_before_child_hooks(hook: typing.Callable[[], None]) -> None:
    try:
        _registry_before_child_hooks.remove(hook)
    except ValueError:
        log.info("before-child-hooks callback %r was unregistered without first being registered", hook)


def unregister_after_child_hooks(hook: typing.Callable[[], None]) -> None:
    try:
        _registry_after_child_hooks.remove(hook)
    except ValueError:
        log.info("after-child-hooks callback %r was unregistered without first being registered", hook)


# Availability: Unix, not WASI, not Android, not iOS.
# Added in version 3.7.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=ddtrace_before_fork, after_in_child=ddtrace_after_in_child, after_in_parent=ddtrace_after_in_parent
    )


_T = typing.TypeVar("_T")


class ResetObject(wrapt.ObjectProxy, typing.Generic[_T]):
    """An object wrapper object that is fork-safe and resets itself after a fork.

    When a Python process forks, a Lock can be in any state, locked or not, by any thread. Since after fork all threads
    are gone, Lock objects needs to be reset. CPython does this with an internal `threading._after_fork` function. We
    use the same mechanism here.

    """

    def __init__(
        self,
        wrapped_class: type[_T],
    ) -> None:
        super().__init__(wrapped_class())
        self._self_wrapped_class = wrapped_class
        _resetable_objects.add(self)

    def _reset_object(self) -> None:
        self.__wrapped__ = self._self_wrapped_class()

    def __reduce__(self) -> "typing.Tuple[type, typing.Tuple[type[_T]]]":  # noqa: UP006
        # A lock/event is process-local, and so it cannot be carried across a process
        # boundary (e.g. by cloudpickle in Ray Serve).
        # The `_thread.lock` primitive is itself unpicklable, so
        # we reconstruct a fresh, unlocked ResetObject, rather than serializing it
        # in the destination process.
        return (ResetObject, (self._self_wrapped_class,))

    # wrapt's ObjectProxy routes __reduce_ex__ to the wrapped object, which is
    # unpicklable for locks; override it so the picklers use __reduce__ above.
    def __reduce_ex__(self, protocol: "typing.SupportsIndex") -> "typing.Tuple[type, typing.Tuple[type[_T]]]":  # noqa: UP006
        return self.__reduce__()


_resetable_objects: weakref.WeakSet[ResetObject] = weakref.WeakSet()


def _reset_objects() -> None:
    for obj in list(_resetable_objects):
        try:
            obj._reset_object()
        except Exception:
            log.exception("Exception ignored in object reset forksafe hook %r", obj)


register(_reset_objects)


def Lock() -> _unpatched.threading_Lock:
    return ResetObject(_unpatched.threading_Lock)


def RLock() -> _unpatched.threading_RLock:
    return ResetObject(_unpatched.threading_RLock)


def Event() -> _unpatched.threading_Event:
    return ResetObject(_unpatched.threading_Event)
