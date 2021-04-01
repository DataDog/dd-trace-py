"""
An API to provide after_in_child fork hooks across all Pythons.
"""
import logging
import os
import typing


log = logging.getLogger(__name__)


_registry = []


def ddtrace_after_in_child():
    # type: () -> None
    global _registry

    for hook in _registry:
        try:
            hook()
        except Exception:
            # Mimic the behaviour of Python's fork hooks.
            log.exception("Exception ignored in forksafe hook %r", hook)


def register(
    after_in_child,  # type: typing.Callable
):
    # type: (...) -> typing.Callable
    """Register a function to be called after fork in the child process."""
    _registry.append(after_in_child)
    return after_in_child


def unregister(
    after_in_child,  # type: typing.Callable
):
    # type: (...) -> None
    """Unregister a function to be called after fork in the child process.

    Raises `ValueError` if the function was not registered.
    """
    _registry.remove(after_in_child)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=ddtrace_after_in_child)
else:
    import threading

    _threading_after_fork = threading._after_fork

    def _after_fork():
        # type: () -> None
        _threading_after_fork()
        ddtrace_after_in_child()

    threading._after_fork = _after_fork
