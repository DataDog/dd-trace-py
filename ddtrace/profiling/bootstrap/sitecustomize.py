# -*- encoding: utf-8 -*-
"""Bootstrapping code that is run when using `ddtrace.profiling.auto`."""

import platform
import sys

from ddtrace.profiling import bootstrap
from ddtrace.profiling import profiler


def start_profiler():
    if hasattr(bootstrap, "profiler"):
        bootstrap.profiler.stop()
    # Export the profiler so we can introspect it if needed
    bootstrap.profiler = profiler.Profiler()
    bootstrap.profiler.start()


# Check if it's linux with 32bit python
if platform.system() == "Linux" and not (sys.maxsize > (1 << 32)):
    print(
        "The Datadog Profiler is not supported on 32-bit Linux systems. "
        "To use the profiler, please upgrade to a 64-bit Linux system. "
        "If you believe this is an error or need assistance, please report it at "
        "https://github.com/DataDog/dd-trace-py/issues"
    )
else:
    start_profiler()
