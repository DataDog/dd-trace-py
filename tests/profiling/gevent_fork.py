import faulthandler
import os
import sys

import gevent.monkey


# On a hang, test_fork_gevent sends SIGABRT to this process and its child: dump every thread's stack (the child
# inherits the handler).
faulthandler.enable()


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
