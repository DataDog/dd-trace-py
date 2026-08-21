"""
An API to provide fork-safe functions.
"""

from _thread import get_ident
import functools
import logging
import os
import typing
import weakref

import wrapt

from ddtrace.internal import _unpatched


log = logging.getLogger(__name__)

# IMPORTANT: Do not change typing.List to list until minimum Python version is 3.11+
# Module-level list[...] in Python 3.10 affects import timing. See packages.py for details.
_registry: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
_registry_before_fork: typing.List[typing.Callable[[], None]] = []  # noqa: UP006
_registry_after_parent: typing.List[typing.Callable[[], None]] = []  # noqa: UP006

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


def run_hooks(registry: list[typing.Callable[[], None]]) -> None:
    for hook in list(registry):
        try:
            hook()
        except Exception:
            # Mimic the behaviour of Python's fork hooks.
            log.exception("Exception ignored in forksafe hook %r", hook)


ddtrace_before_fork = functools.partial(run_hooks, _registry_before_fork)
ddtrace_after_in_child = functools.partial(run_hooks, _registry)
ddtrace_after_in_parent = functools.partial(run_hooks, _registry_after_parent)


def register_hook(registry, hook):
    registry.append(hook)
    return hook


register_before_fork = functools.partial(register_hook, _registry_before_fork)
register = functools.partial(register_hook, _registry)
register_after_parent = functools.partial(register_hook, _registry_after_parent)

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
        super(ResetObject, self).__init__(wrapped_class())
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


class ResetLock(ResetObject[_unpatched.threading_Lock]):
    """A lock that resets after fork while active contexts unwind safely."""

    def __init__(self) -> None:
        super().__init__(_unpatched.threading_Lock)
        self._self_context_depths: dict[int, int] = {}
        self._self_fork_owner: typing.Optional[int] = None
        self._self_fork_depth = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        thread_id = get_ident()
        if self._self_fork_owner == thread_id:
            self._self_fork_depth += 1
            return True
        return self.__wrapped__.acquire(blocking, timeout)

    def release(self) -> None:
        if self._self_fork_owner == get_ident():
            self._self_fork_depth -= 1
            if self._self_fork_depth == 0:
                self._self_fork_owner = None
                self.__wrapped__.release()
            return
        self.__wrapped__.release()

    def __enter__(self) -> typing.Any:
        entered = self.acquire()
        thread_id = get_ident()
        self._self_context_depths[thread_id] = self._self_context_depths.get(thread_id, 0) + 1
        return entered

    def __exit__(self, *args: typing.Any) -> typing.Any:
        thread_id = get_ident()
        context_depth = self._self_context_depths[thread_id] - 1
        if context_depth:
            self._self_context_depths[thread_id] = context_depth
        else:
            del self._self_context_depths[thread_id]
        self.release()
        return None

    def __reduce__(self) -> typing.Any:
        return (ResetLock, ())

    def _reset_object(self) -> None:
        thread_id = get_ident()
        context_depth = self._self_context_depths.get(thread_id, 0)
        if self._self_fork_owner == thread_id:
            fork_depth = max(context_depth, self._self_fork_depth)
        else:
            fork_depth = context_depth
        self._self_context_depths = {thread_id: context_depth} if context_depth else {}
        super()._reset_object()
        self._self_fork_owner = None
        self._self_fork_depth = 0
        if fork_depth:
            self.__wrapped__.acquire()
            self._self_fork_owner = thread_id
            self._self_fork_depth = fork_depth


_resetable_objects: weakref.WeakSet[ResetObject] = weakref.WeakSet()


def _reset_objects() -> None:
    for obj in list(_resetable_objects):
        try:
            obj._reset_object()
        except Exception:
            log.exception("Exception ignored in object reset forksafe hook %r", obj)


register(_reset_objects)


def Lock() -> _unpatched.threading_Lock:
    return ResetLock()


def Event() -> _unpatched.threading_Event:
    return ResetObject(_unpatched.threading_Event)
