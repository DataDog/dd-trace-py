"""
An API to provide fork-safe functions.
"""

import functools
import inspect
import logging
import os
import typing
import weakref

import wrapt

from ddtrace.internal import _unpatched
from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.locks import RLock as _RLock
from ddtrace.internal.native.exceptions import is_panic_exception


log = logging.getLogger(__name__)

_R = typing.TypeVar("_R")

# IMPORTANT: Do not change typing.List to list until minimum Python version is 3.11+
# Module-level list[...] in Python 3.10 affects import timing. See packages.py for details.
_registry: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
_registry_before_fork: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
_registry_after_parent: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
# Generator functions that wrap the after-in-child hooks: the code before the
# yield runs before the hooks, in registration order, and the code after it runs
# once they have all completed, in reverse order. Either part can be empty.
_registry_child_wrappers: typing.List[typing.Callable[[], typing.Generator[None, None, None]]] = []  # noqa: UP006

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


def _call_hook(hook: typing.Any, call: typing.Callable[..., _R], *args: typing.Any) -> typing.Optional[_R]:
    # Calls call(*args) on behalf of hook, logging and ignoring its exceptions.
    # Returns None if the call failed.
    try:
        return call(*args)
    except Exception:
        # Mimic the behaviour of Python's fork hooks.
        log.exception("Exception ignored in forksafe hook %r", hook)
    except BaseException as e:
        # A native hook can raise pyo3_runtime.PanicException. We handle it
        # to allow other hooks to continue in this expected case.
        if not is_panic_exception(e):
            raise
        log.exception("Exception ignored in forksafe hook %r", hook)
    return None


def run_hooks(registry: list[typing.Callable[[], None]]) -> None:
    for hook in list(registry):
        _call_hook(hook, hook)


def _start_wrapper(
    wrapper: typing.Callable[[], typing.Generator[None, None, None]],
) -> typing.Generator[None, None, None]:
    gen = wrapper()
    try:
        gen.send(None)
    except StopIteration:
        raise RuntimeError("child hook wrapper %r returned without yielding" % wrapper) from None
    return gen


def _resume_wrapper(
    wrapper: typing.Callable[[], typing.Generator[None, None, None]], gen: typing.Generator[None, None, None]
) -> None:
    try:
        gen.send(None)
    except StopIteration:
        return
    gen.close()
    raise RuntimeError("child hook wrapper %r yielded more than once" % wrapper)


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
    if not _fork_lock._is_owned():  # type: ignore[attr-defined]
        # An exception (e.g. KeyboardInterrupt) can interrupt the blocking
        # acquire in ddtrace_before_fork, and CPython reports it and forks
        # anyway. Another thread may be in the middle of its own fork, so
        # running the after-parent hooks here would undo its before-fork work
        # (e.g. restart the threads it is stopping).
        return

    try:
        run_hooks(_registry_after_parent)
    finally:
        _fork_lock.release()


def run_child_hooks() -> None:
    global _in_child_hook, _fork_lock

    # The child is single-threaded, so a fresh lock is equivalent to releasing
    # the inherited one. Unlike a release, it also covers callers that run the
    # child hooks without a matching before-fork call (e.g. uWSGI workers
    # forked by the master at the C level).
    _fork_lock = _RLock()

    _in_child_hook = True
    # A wrapper that fails before its yield is skipped, and one that fails
    # after it does not prevent the remaining ones from resuming, so that a
    # faulty wrapper cannot stop the hooks from running. _call_hook re-raises a
    # BaseException that is not a panic (e.g. KeyboardInterrupt); such errors
    # are collected and raised only once every started wrapper has resumed, so
    # that none of them misses its cleanup.
    started = []
    errors: list[BaseException] = []
    try:
        for wrapper in list(_registry_child_wrappers):
            gen = _call_hook(wrapper, _start_wrapper, wrapper)
            if gen is not None:
                started.append((wrapper, gen))
        run_hooks(_registry)
    except BaseException as e:
        errors.append(e)

    for wrapper, gen in reversed(started):
        try:
            _call_hook(wrapper, _resume_wrapper, wrapper, gen)
        except BaseException as e:
            errors.append(e)

    _in_child_hook = False

    if errors:
        _raise_errors(errors)


def _raise_errors(errors: list[BaseException]) -> typing.NoReturn:
    if len(errors) == 1:
        raise errors[0]

    if is_at_least_py(3, 11):
        raise BaseExceptionGroup("Exceptions raised by after-in-child fork hooks", errors)  # type: ignore[name-defined]  # noqa: F821

    # Without exception groups only one exception can propagate, so log the
    # others to keep them visible.
    for error in errors[1:]:
        log.error("Exception raised by after-in-child fork hook", exc_info=error)
    raise errors[0]


ddtrace_after_in_child = run_child_hooks


def register_hook(registry, hook):
    registry.append(hook)
    return hook


register_before_fork = functools.partial(register_hook, _registry_before_fork)


_H = typing.TypeVar("_H", bound=typing.Callable[[], typing.Any])


def register(hook: _H) -> _H:
    """Register an after-in-child hook.

    A generator function is registered as a wrapper around the other
    after-in-child hooks instead: it runs up to its yield before them and
    resumes after they have all completed.

    The check uses inspect.isgeneratorfunction, so only an actual generator
    function, or a bound method or functools.partial of one, is detected. A
    callable that merely returns a generator (e.g. a decorator wrapping a
    generator function, an object whose __call__ yields, or a lambda) is
    registered as a plain hook, and since nothing iterates the generator it
    returns, none of its code runs.
    """
    if inspect.isgeneratorfunction(hook):
        _registry_child_wrappers.append(hook)
    else:
        _registry.append(hook)
    return hook


register_after_parent = functools.partial(register_hook, _registry_after_parent)

register(set_fork_child)
register_after_parent(set_forked)


def unregister(after_in_child: typing.Callable[[], typing.Any]) -> None:
    registry = _registry_child_wrappers if inspect.isgeneratorfunction(after_in_child) else _registry
    try:
        registry.remove(after_in_child)
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
