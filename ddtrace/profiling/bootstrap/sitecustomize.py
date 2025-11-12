# -*- encoding: utf-8 -*-
"""Bootstrapping code that is run when using `ddtrace.profiling.auto`."""

import platform
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.profiling import bootstrap
from ddtrace.profiling import profiler


LOG = get_logger(__name__)


def start_profiler():
    """Start profiler if enabled by configuration.

    This function respects DD_PROFILING_ENABLED setting and will not start
    the profiler if it's disabled, even when called explicitly.
    """
    from ddtrace.settings.profiling import config as profiling_config

    if not profiling_config.enabled:
        LOG.debug("start_profiler() called but DD_PROFILING_ENABLED is disabled")
        return

    if hasattr(bootstrap, "profiler") and bootstrap.profiler is not None:
        try:
            bootstrap.profiler.stop()
        except Exception:
            LOG.debug("Failed to stop existing profiler", exc_info=True)

    # Export the profiler so we can introspect it if needed
    bootstrap.profiler = profiler.Profiler()
    bootstrap.profiler.start()
    LOG.debug("Profiler started successfully")


def _maybe_start_profiler():
    """Auto-start profiler only if enabled and platform is supported."""
    if platform.system() == "Linux" and not (sys.maxsize > (1 << 32)):
        LOG.error(
            "The Datadog Profiler is not supported on 32-bit Linux systems. "
            "To use the profiler, please upgrade to a 64-bit Linux system. "
            "If you believe this is an error or need assistance, please report it at "
            "https://github.com/DataDog/dd-trace-py/issues"
        )
        return False

    if platform.system() == "Windows":
        LOG.error(
            "The Datadog Profiler is not supported on Windows. "
            "To use the profiler, please use a 64-bit Linux or macOS system. "
            "If you need assistance related to Windows support for the Profiler, please open a ticket at "
            "https://github.com/DataDog/dd-trace-py/issues"
        )
        return False

    # Check config before starting
    from ddtrace.settings.profiling import config as profiling_config

    if not profiling_config.enabled:
        LOG.debug("Profiler auto-start skipped: DD_PROFILING_ENABLED is disabled")
        return False

    start_profiler()
    return True


# Auto-start with config check
_profiler_started = _maybe_start_profiler()
