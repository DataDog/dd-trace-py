# -*- encoding: utf-8 -*-
"""
An API to provide atexit functionalities
"""

from __future__ import absolute_import

import atexit
import logging
import signal
import threading
import typing

from ddtrace.internal.utils import signals


log = logging.getLogger(__name__)

# Maps originally registered callables to their exception-safe wrappers,
# so that unregister can find the right function to remove.
_atexit_wrapped: dict[typing.Callable, typing.Callable] = {}


def _make_safe_wrapper(func: typing.Callable) -> typing.Callable:
    """Wrap func so that any exception it raises is swallowed at exit time
    unless DD_TESTING_RAISE is set, in which case it is re-raised to allow
    tests to catch regressions in atexit handlers.
    """

    def _wrapped(*args: typing.Any, **kwargs: typing.Any) -> None:
        try:
            func(*args, **kwargs)
        except Exception:
            # Lazy import to avoid circular dependencies at module load time.
            from ddtrace import config

            if config._raise:
                raise

    return _wrapped


def register(func: typing.Callable, *args: typing.Any, **kwargs: typing.Any) -> typing.Callable:
    """Register a function to be executed upon normal program termination.

    This wraps the standard library's atexit.register but respects uwsgi's
    --skip-atexit flag when running under uwsgi. When --skip-atexit is set,
    the registration is skipped to avoid running cleanup code during process
    shutdown, which can cause crashes.

    Registered callbacks are wrapped so that any unexpected exception they
    raise is silently swallowed in production and only re-raised when
    DD_TESTING_RAISE is set.
    """
    from ddtrace.internal import uwsgi

    if uwsgi.should_register_atexit():
        wrapped = _make_safe_wrapper(func)
        _atexit_wrapped[func] = wrapped
        return atexit.register(wrapped, *args, **kwargs)
    return func


def unregister(func: typing.Callable) -> None:
    """Unregister *func* from atexit, handling the safe-wrapper indirection."""
    wrapped = _atexit_wrapped.pop(func, None)
    atexit.unregister(wrapped if wrapped is not None else func)


# registers a function to be called when an exit signal (TERM or INT) or received.
def register_on_exit_signal(f: typing.Callable) -> None:
    def handle_exit(sig: int, frame: typing.Any) -> None:
        try:
            f()
        except Exception:
            # Lazy import to avoid circular dependencies at module load time.
            from ddtrace import config

            if config._raise:
                raise

    if threading.current_thread() is threading.main_thread():
        try:
            signals.handle_signal(signal.SIGTERM, handle_exit)
            signals.handle_signal(signal.SIGINT, handle_exit)
        except Exception:
            # We catch a general exception here because we don't know
            # what might go wrong, but we don't want to stop
            # normal program execution based upon failing to register
            # a signal handler.
            log.debug("Encountered an exception while registering a signal", exc_info=True)
