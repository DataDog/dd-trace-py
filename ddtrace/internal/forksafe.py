"""
An API to provide after_in_child fork hooks across all Pythons.
"""
import logging
import os


__all__ = [
    "ddtrace_after_in_child",
    "register",
]


log = logging.getLogger(__name__)


registry = []


def ddtrace_after_in_child():
    global registry

    for hook in registry:
        try:
            hook()
        except Exception:
            # Mimic the behaviour of Python's fork hooks.
            log.exception("Exception ignored in forksafe hook %r", hook)


def _register(hook):
    if hook not in registry:
        registry.append(hook)


def register(after_in_child):
    _register(after_in_child)
    return lambda f: f


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=ddtrace_after_in_child)
else:
    import threading

    _threading_after_fork = threading._after_fork

    def _after_fork():
        _threading_after_fork()
        ddtrace_after_in_child()

    threading._after_fork = _after_fork
