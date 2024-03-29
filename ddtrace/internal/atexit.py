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


register = atexit.register
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
