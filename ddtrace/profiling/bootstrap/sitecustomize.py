# -*- encoding: utf-8 -*-
"""Bootstrapping code that is run when using `ddtrace.profiling.auto`."""

import platform
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.profiling import bootstrap


LOG = get_logger(__name__)


def start_profiler() -> None:
    try:
        from ddtrace.profiling import profiler
    except ImportError as e:
        LOG.warning(
            "The Datadog Profiler could not be started because native extensions are not "
            "available on this Python version: %s",
            e,
        )
        return

    if hasattr(bootstrap, "profiler"):
        bootstrap.profiler.stop()  # pyright: ignore[reportAttributeAccessIssue, reportCallIssue]

    # Export the profiler so we can introspect it if needed
    profiler_instance = profiler.Profiler()
    bootstrap.profiler = profiler_instance  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
    bootstrap.profiler.start()  # type: ignore[attr-defined]  # pyright: ignore[reportCallIssue]


if platform.system() == "Linux" and not (sys.maxsize > (1 << 32)):
    LOG.error(
        "The Datadog Profiler is not supported on 32-bit Linux systems. "
        "To use the profiler, please upgrade to a 64-bit Linux system. "
        "If you believe this is an error or need assistance, please report it at "
        "https://github.com/DataDog/dd-trace-py/issues"
    )
elif platform.system() == "Windows":
    LOG.error(
        "The Datadog Profiler is not supported on Windows. "
        "To use the profiler, please use a 64-bit Linux or macOS system. "
        "If you need assistance related to Windows support for the Profiler, please open a ticket at "
        "https://github.com/DataDog/dd-trace-py/issues"
    )
else:
    start_profiler()
