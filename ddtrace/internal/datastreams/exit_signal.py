"""One SIGTERM/SIGINT handler shared by every data streams processor.

Kept out of the processor module so startup can install it on the main thread
without importing the processor.
"""

import signal
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.atexit import register_on_exit_signal


# Shutdown callbacks of the live processors. One shared handler serves them all, so
# re-creating a processor (remote config re-enable) does not chain another handler.
shutdown_callbacks: set[Callable[[], None]] = set()
# The SIGTERM and SIGINT handlers in place after the last registration.
_exit_signal_handlers: Optional[tuple[Any, Any]] = None


def _run_shutdown_callbacks() -> None:
    for callback in list(shutdown_callbacks):
        callback()


def register_exit_signal_handler() -> None:
    """Install the shared handler, again only if it never got installed or the app replaced it.

    Installing only works on the main thread; elsewhere this is a no-op.
    """
    global _exit_signal_handlers
    current_handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    if _exit_signal_handlers is None or current_handlers != _exit_signal_handlers:
        _exit_signal_handlers = register_on_exit_signal(_run_shutdown_callbacks)
