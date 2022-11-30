import os
import sys

import gevent.monkey


gevent.monkey.patch_all()

from ddtrace.profiling import profiler  # noqa


p = profiler.Profiler().start(profile_children=True)

pid = os.fork()
if pid == 0:
    print("Exiting")
else:
    print(pid)
    pid, status = os.waitpid(pid, 0)
    print("Exited")
    sys.exit(os.WEXITSTATUS(status))
