# -*- encoding: utf-8 -*-
"""
An API to provide atexit functionalities
"""
from __future__ import absolute_import

import atexit
import logging
import signal
import threading
import typing  # noqa:F401

from ddtrace.internal.utils import signals


log = logging.getLogger(__name__)


unregistered_signals = set()  # type: typing.Set[typing.Callable]


def register(func, register_signal=False):
    # type: (typing.Callable, bool) -> None
    """
    Register a function to be called when the program exits.
    """
    atexit.register(func)
    if register_signal:
        # Register the function to be called when an exit signal (TERM or INT) is received.
        # Default value is False to avoid breaking existing behavior.
        register_on_exit_signal(func)
        unregistered_signals.discard(func)


def unregister(func):
    # type: (typing.Callable) -> None
    """
    Unregister a function to be called when the program exits.
    """
    unregistered_signals.add(func)
    atexit.unregister(func)


# registers a function to be called when an exit signal (TERM or INT) or received.
def register_on_exit_signal(f):
    def handle_exit(sig, frame):
        if f not in unregistered_signals:
            f()

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
