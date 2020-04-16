# -*- encoding: utf-8 -*-
"""Bootstrapping code that is run when using the `pyddprofile`."""
import atexit
import os

from ddtrace.profiling import bootstrap
from ddtrace.profiling import profiler
from ddtrace.vendor import six


def start_profiler():
    if hasattr(bootstrap, "profiler"):
        # Stop the previous profiler (stop thread if ever needed, but also unpatch classes)
        # Python 2 does not have unregister so we can't use it all the time
        if six.PY3:
            atexit.unregister(bootstrap.profiler.stop)
        bootstrap.profiler.stop()
    # Export the profiler so we can introspect it if needed
    bootstrap.profiler = profiler.Profiler()
    bootstrap.profiler.start()
    atexit.register(bootstrap.profiler.stop)


start_profiler()
# When forking, all threads are stop in the child.
# Restart a new profiler.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=start_profiler)
