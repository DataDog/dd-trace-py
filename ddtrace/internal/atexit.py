"""
An API to provide atexit functionalities
"""

import atexit
import logging
import signal
import typing

from ddtrace.internal.native import config as _native_config
from ddtrace.internal.threads import is_main_thread
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
            if _native_config.get_raise():
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
def register_on_exit_signal(f: typing.Callable) -> typing.Optional[tuple[typing.Any, typing.Any]]:
    """Installs a function to be called when an exit signal (TERM or INT) is received.

    Returns the SIGTERM and SIGINT handlers in place after installation, or None if
    nothing was installed. The SIGINT handler can be one we did not install, e.g.
    default_int_handler.
    """

    def handle_exit(sig: int, frame: typing.Any) -> None:
        try:
            f()
        except Exception:
            if _native_config.get_raise():
                raise

    if not is_main_thread():
        return None

    # Each signal is installed on its own, and failures are not reported as "nothing
    # installed". A caller that registers once would then register again and chain a
    # second handler that nothing can remove.
    # We catch a general exception here because we don't know what might go wrong, but we
    # don't want to stop normal program execution based upon failing to register a signal
    # handler.
    try:
        signals.handle_signal(signal.SIGTERM, handle_exit)
        sigterm_handler = signal.getsignal(signal.SIGTERM)
    except Exception:
        log.debug("Encountered an exception while registering SIGTERM", exc_info=True)
        # Stop here so that nothing is installed at all. A caller told "nothing
        # installed" could register again, and a SIGINT handler put in now could not be
        # removed before it does.
        return None

    try:
        # Skipping SIGINT when default_int_handler is installed allows asyncio.Runner
        # to install its own handler; cleanup on KeyboardInterrupt is covered by atexit.
        if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
            signals.handle_signal(signal.SIGINT, handle_exit)
    except Exception:
        log.debug("Encountered an exception while registering SIGINT", exc_info=True)

    return sigterm_handler, signal.getsignal(signal.SIGINT)


class ExitSignalCallbacks:
    """One exit-signal handler running a set of callbacks; re-installs only if the app replaced it."""

    def __init__(self) -> None:
        self._callbacks: set[typing.Callable[[], None]] = set()
        # The SIGTERM and SIGINT handlers in place after the last installation.
        self._installed_handlers: typing.Optional[tuple[typing.Any, typing.Any]] = None

    def add(self, f: typing.Callable[[], None]) -> None:
        self._callbacks.add(f)

    def discard(self, f: typing.Callable[[], None]) -> None:
        self._callbacks.discard(f)

    def _run(self) -> None:
        # Snapshot: a callback may discard itself while running.
        for callback in list(self._callbacks):
            callback()

    def install(self) -> None:
        """Install the handler, again only if it never got installed or the app replaced it.

        Installing only works on the main thread; elsewhere this is a no-op.
        """
        # Off the main thread nothing installs; keep the saved state so a later call does not chain.
        if not is_main_thread():
            return
        current_handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        if self._installed_handlers is None or current_handlers != self._installed_handlers:
            self._installed_handlers = register_on_exit_signal(self._run)
