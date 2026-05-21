import os
import sys

import gevent.monkey


# DEV: patch_all() is called BEFORE importing ddtrace intentionally.
# This models the hostile real-world ordering where a customer monkey-patches
# gevent at the very top of their entrypoint, before any other imports.
# contrast with tests/profiling/simple_program_gevent.py which shows the
# *correct* (ddtrace-first) ordering. Do NOT "fix" this import order.
# The regression guard for this scenario is:
#   tests/internal/test_unpatched.py::test_unpatched_primitives_after_gevent_patch_all
gevent.monkey.patch_all()

from ddtrace.profiling import profiler  # noqa:E402,F401


p = profiler.Profiler()
p.start()

pid = os.fork()
if pid == 0:
    print("Exiting")
else:
    print(pid)
    pid, status = os.waitpid(pid, 0)
    print("Exited")
    sys.exit(os.WEXITSTATUS(status))
