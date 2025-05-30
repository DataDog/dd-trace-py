import os
import sys
import threading

from ddtrace.internal import service
import ddtrace.profiling.auto
import ddtrace.profiling.bootstrap
import ddtrace.profiling.profiler


lock = threading.Lock()
lock.acquire()


assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING


child_pid = os.fork()
if child_pid == 0:
    # Release it
    lock.release()

    # We track this one though
    lock = threading.Lock()
    lock.acquire()
    lock.release()
else:
    lock.release()
    assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING
    print(child_pid)
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
