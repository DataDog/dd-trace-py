"""Install the profiler without starting it."""

import platform
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import ServiceStatus
import ddtrace.profiling as profiling
from ddtrace.profiling import bootstrap


LOG = get_logger(__name__)


def _platform_supported() -> bool:
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
    return True


def install_profiler() -> None:
    """Install profiler patches and signal handlers. Do not start collection."""
    if not _platform_supported():
        return
    if not profiling.is_available:
        LOG.warning(
            "The Datadog Profiler could not be installed because native extensions are not "
            "available on this Python version: %s",
            profiling.failure_msg,
        )
        return

    from ddtrace.profiling import profiler

    if hasattr(bootstrap, "profiler"):
        existing = bootstrap.profiler  # pyright: ignore[reportAttributeAccessIssue]
        if existing.status != ServiceStatus.RUNNING:
            existing.install()
        return

    profiler_instance = profiler.Profiler()
    bootstrap.profiler = profiler_instance  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
    profiler_instance.install()
