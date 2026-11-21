"""DSM's shared exit-signal callbacks; a leaf module so processor and startup can import it without cycles."""

from ddtrace.internal.atexit import ExitSignalCallbacks


# Shutdown callbacks of the live processors behind one handler, so re-creating a
# processor (remote config re-enable) does not chain another.
exit_signal_callbacks = ExitSignalCallbacks()
