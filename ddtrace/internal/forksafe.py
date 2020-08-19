"""
An API to provide after_in_child fork hooks across all Pythons.
"""
import functools
import logging
import os
import threading


__all__ = [
    "ddtrace_after_in_child",
    "register",
]


log = logging.getLogger(__name__)

BUILTIN_FORK_HOOKS = hasattr(os, "register_at_fork")

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


if BUILTIN_FORK_HOOKS:

    def register(after_in_child):
        _register(after_in_child)
        return lambda f: f

    os.register_at_fork(after_in_child=ddtrace_after_in_child)
else:
    PID = os.getpid()
    PID_LOCK = threading.Lock()

    def register(after_in_child):
        """Decorator that registers a function `after_in_child` that will be
        called in the child process when a fork occurs.

        Decorator usage::
            def after_fork():
                # update fork-sensitive state
                pass

            @forksafe.register(after_in_child=after_fork)
            def fork_sensitive_fn():
                # after_fork is guaranteed to be called by this point
                # if a fork occurred and we're in the child.
                pass
        """
        _register(after_in_child)

        def wrapper(func):
            @functools.wraps(func)
            def forksafe_func(*args, **kwargs):
                global PID

                # A lock is required here to ensure that the hooks
                # are only called once.
                with PID_LOCK:
                    pid = os.getpid()

                    # Check the global pid
                    if pid != PID:
                        # Call ALL the hooks.
                        ddtrace_after_in_child()
                        PID = pid
                return func(*args, **kwargs)

            # Set a flag to use to perform sanity checks.
            forksafe_func._is_forksafe = True
            return forksafe_func

        return wrapper
