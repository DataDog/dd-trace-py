import inspect
import functools
import os
import threading


__all__ = [
    "call_nocheck",
    "ddtrace_after_in_child",
    "register",
]


BUILTIN_FORK_HOOKS = hasattr(os, "register_at_fork")

if BUILTIN_FORK_HOOKS:
    registry = []

    def ddtrace_after_in_child():
        global registry

        for fn, hook in registry:
            hook()
            fn._fork_occurred = True

    def register(after_in_child):
        # TODO: this function won't work if not used as a decorator.
        # eg: register(hook) won't result in hook being called.
        def wrapper(func):
            args = inspect.getfullargspec(func)[0]
            include_arg = "is_in_child_after_fork" in args

            @functools.wraps(func)
            def fsfunc(*args, **kwargs):
                fork_occurred = fsfunc._fork_occurred
                fsfunc._fork_occurred = False
                if include_arg:
                    return func(*args, is_in_child_after_fork=fork_occurred, **kwargs)
                else:
                    return func(*args, **kwargs)

            fsfunc._fork_occurred = False
            registry.append((fsfunc, after_in_child))
            return fsfunc

        return wrapper

    def call_nocheck(f, *args, **kwargs):
        return f(*args, **kwargs)

    os.register_at_fork(after_in_child=ddtrace_after_in_child)
else:
    PID = os.getpid()
    PID_LOCK = threading.Lock()
    registry = []

    def ddtrace_after_in_child():
        global registry

        for hook in registry:
            hook()

    def register(after_in_child):
        registry.append(after_in_child)

        def wrapper(func):
            args, _, _, _ = inspect.getargspec(func)
            include_arg = "is_in_child_after_fork" in args

            @functools.wraps(func)
            def fsfunc(*args, **kwargs):
                global PID
                fork_occurred = False

                if kwargs.get("check_pid", True):
                    with PID_LOCK:
                        pid = os.getpid()

                        # Check the global pid
                        if pid != PID:
                            # Call ALL the hooks.
                            ddtrace_after_in_child()
                            PID = pid
                            fork_occurred = True

                    # Check if a fork occurred and the hooks were called
                    # by another function.
                    if fsfunc._pid != pid:
                        fsfunc._pid = pid
                        fork_occurred = True

                if include_arg:
                    return func(*args, is_in_child_after_fork=fork_occurred, **kwargs)
                else:
                    return func(*args, **kwargs)

            fsfunc._pid = PID
            # Set a flag to use to perform sanity checks.
            fsfunc._is_forksafe = True
            return fsfunc

        return wrapper

    def call_nocheck(f, *args, **kwargs):
        assert hasattr(f, "_is_forksafe")
        return f(*args, check_pid=False, **kwargs)
