"""
Crash tracking service API.

Crash tracking monitors your application for native crashes (segfaults, etc.)
and automatically reports them to Datadog. It is normally started automatically
by ``ddtrace-run`` when ``DD_CRASHTRACKING_ENABLED`` is not set to ``false``.

To start crash tracking manually without ``ddtrace-run``::

    from ddtrace.crashtracking import CrashTracking
    CrashTracking.enable()

Configuration is done through environment variables (see
``ddtrace.internal.settings.crashtracker`` for available options). Calling
``enable()`` will start crash tracking regardless of the
``DD_CRASHTRACKING_ENABLED`` setting, since an explicit API call is considered
a stronger signal.
"""

from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CrashTracking:
    """
    Crash tracking service API.

    This is normally started automatically by ``ddtrace-run`` when
    ``DD_CRASHTRACKING_ENABLED`` is not set to ``false``.

        from ddtrace.crashtracking import CrashTracking
        CrashTracking.enable()
    """

    @staticmethod
    def enable(tags: Optional[dict[str, str]] = None) -> bool:
        """
        Enable crash tracking.

        Starts the native crash tracker to monitor for segfaults and other
        fatal signals, reporting them to Datadog. This method is idempotent;
        if crash tracking is already running, it returns ``True`` without
        restarting.

        This bypasses the ``DD_CRASHTRACKING_ENABLED`` environment variable,
        since calling ``enable()`` from code is an explicit opt-in.

        :param tags: Optional additional tags to attach to crash reports.
        :returns: ``True`` if crash tracking was successfully enabled (or was
            already running), ``False`` otherwise (native extensions
            not available).
        """
        from ddtrace.internal.core import crashtracking

        if crashtracking.is_started():
            return True

        try:
            result = crashtracking.start(additional_tags=tags, _enabled_override=True)
            if not result:
                log.warning("Failed to enable crash tracking")
            return result
        except Exception:
            # Error shows in client logs, but that is what we want because this is
            # an explicit opt-in call
            log.error("Failed to enable crash tracking", exc_info=True)
            return False

    @staticmethod
    def is_enabled() -> bool:
        """
        Check whether crash tracking is currently running.

        :returns: ``True`` if the crash tracker is active, ``False`` otherwise.
        """
        from ddtrace.internal.core import crashtracking

        return crashtracking.is_started()


__all__ = ["CrashTracking"]
