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


def register(func, *args, **kwargs):
    """Register a function to be executed upon normal program termination.

    This wraps the standard library's atexit.register but respects uwsgi's
    --skip-atexit flag when running under uwsgi. When --skip-atexit is set,
    the registration is skipped to avoid running cleanup code during process
    shutdown, which can cause crashes.
    """
    from ddtrace.internal import uwsgi

    if uwsgi.should_register_atexit():
        return atexit.register(func, *args, **kwargs)
    return func


unregister = atexit.unregister


# registers a function to be called when an exit signal (TERM or INT) or received.
def register_on_exit_signal(f):
    def handle_exit(sig, frame):
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
