import signal
import sys


def _is_interpreter_finalizing():
    """Check if the Python interpreter is in the process of shutting down.

    Calling signal.signal() during interpreter finalization can cause a
    SIGSEGV because Python's internal signal state may have been freed.
    This is a known issue with grpc, faulthandler, and other libraries
    that manipulate signal handlers during shutdown.

    Returns True if the interpreter is finalizing and signal operations
    should be avoided.
    """
    # Python 3.12+ has sys._is_finalizing()
    # Python 3.6-3.11 has sys.is_finalizing()
    if hasattr(sys, "_is_finalizing"):
        return sys._is_finalizing()
    elif hasattr(sys, "is_finalizing"):
        return sys.is_finalizing()
    # Fallback: assume not finalizing
    return False


def handle_signal(sig, f):
    """
    Returns a signal of type `sig` with function `f`, if there are
    no previously defined signals.

    Else, wraps the given signal with the previously defined one,
    so no signals are overridden.

    AIDEV-NOTE: This function checks for interpreter finalization before
    calling signal.signal() to avoid SIGSEGV crashes. During Python shutdown,
    the interpreter's signal handling state may be freed, and calling
    signal.signal() or signal.getsignal() can crash with a NULL pointer
    dereference at offset 0x60 (the SIGABRT handler slot).
    See: https://github.com/grpc/grpc/issues/39520
    """
    # AIDEV-NOTE: Don't attempt to modify signals during interpreter shutdown.
    # This can cause SIGSEGV when Python's signal state has been freed.
    if _is_interpreter_finalizing():
        return None

    try:
        old_signal = signal.getsignal(sig)
    except (OSError, ValueError):
        # Signal operations may fail during shutdown
        return None

    def wrap_signals(*args, **kwargs):
        if old_signal is not None:
            old_signal(*args, **kwargs)
        f(*args, **kwargs)

    # Return the incoming signal if any of the following cases happens:
    # - old signal does not exist,
    # - old signal is the same as the incoming, or
    # - old signal is our wrapper.
    # This avoids multiple signal calling and infinite wrapping.
    try:
        if not callable(old_signal) or old_signal == f or old_signal == wrap_signals:
            return signal.signal(sig, f)
        return signal.signal(sig, wrap_signals)
    except (OSError, ValueError):
        # Signal operations may fail during shutdown
        return None
