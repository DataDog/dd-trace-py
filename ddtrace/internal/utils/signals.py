import signal


# Track which signals have had the base exit handler installed
_exit_handlers_installed = set()


def _raise_default(signum, frame):
    signal.signal(signum, signal.SIG_DFL)
    signal.raise_signal(signum)


def _install_exit_handler(sig):
    """Install a base handler that exits the process after all other handlers complete."""
    if sig in _exit_handlers_installed:
        return

    # Install the exit handler as the base (will run last due to wrapping order)
    signal.signal(sig, _raise_default)
    _exit_handlers_installed.add(sig)


def handle_signal(sig, f):
    """
    Returns a signal of type `sig` with function `f`, if there are
    no previously defined signals.

    Else, wraps the given signal with the previously defined one,
    so no signals are overridden.

    For SIGTERM and SIGINT, automatically ensures the process exits after
    all handlers complete by installing a base exit handler on first use.
    """
    old_signal = signal.getsignal(sig)

    # For termination signals, ensure we have a base handler that will exit the process
    if sig in (signal.SIGTERM, signal.SIGINT) and not callable(old_signal):
        _install_exit_handler(sig)
        old_signal = signal.getsignal(sig)

    def wrap_signals(*args, **kwargs):
        # Execute new handler first, then old handler.
        # This ensures handlers registered later run before handlers registered earlier,
        # so the base exit handler (registered first) runs last (after all cleanup).
        f(*args, **kwargs)
        if old_signal is not None:
            old_signal(*args, **kwargs)

    # Return the incoming signal if any of the following cases happens:
    # - old signal does not exist,
    # - old signal is the same as the incoming, or
    # - old signal is our wrapper.
    # This avoids multiple signal calling and infinite wrapping.
    if not callable(old_signal) or old_signal == f or old_signal == wrap_signals:
        return signal.signal(sig, f)

    return signal.signal(sig, wrap_signals)
