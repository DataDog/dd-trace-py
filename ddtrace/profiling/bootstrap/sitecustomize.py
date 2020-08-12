# -*- encoding: utf-8 -*-
"""Bootstrapping code that is run when using the `pyddprofile`."""
import os

from ddtrace.profiling import bootstrap
from ddtrace.profiling import profiler


def start_profiler():
    if hasattr(bootstrap, "profiler"):
        bootstrap.profiler.stop()
    # Export the profiler so we can introspect it if needed
    bootstrap.profiler = profiler.Profiler()
    bootstrap.profiler.start()


start_profiler()
# When forking, all threads are stop in the child.
# Restart a new profiler.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=start_profiler)
