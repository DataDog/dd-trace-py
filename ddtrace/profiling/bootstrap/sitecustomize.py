# -*- encoding: utf-8 -*-
"""Bootstrapping code that is run when using `ddtrace.profiling.auto`."""

import platform
import sys

from ddtrace.internal.logger import get_logger
from ddtrace.profiling import bootstrap
from ddtrace.profiling import profiler


LOG = get_logger(__name__)


def start_profiler():
    LOG.debug("[dd.profiling] start_profiler: initializing Profiler")
    if hasattr(bootstrap, "profiler"):
        LOG.debug("[dd.profiling] start_profiler: stopping existing profiler before restart")
        bootstrap.profiler.stop()
    # Export the profiler so we can introspect it if needed
    try:
        bootstrap.profiler = profiler.Profiler()
        LOG.debug("[dd.profiling] start_profiler: Profiler() constructed successfully")
    except Exception:
        LOG.error("[dd.profiling] start_profiler: Profiler() construction failed", exc_info=True)
        return
    try:
        bootstrap.profiler.start()
        LOG.debug("[dd.profiling] start_profiler: profiler.start() completed successfully")
    except Exception:
        LOG.error("[dd.profiling] start_profiler: profiler.start() failed", exc_info=True)


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
    LOG.debug("[dd.profiling] sitecustomize: platform=%r, starting profiler", platform.system())
    start_profiler()
