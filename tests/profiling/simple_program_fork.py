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
    # Child process
    print(f"{os.getpid()}:start", flush=True)
    # Release it
    lock.release()

    # We track this one though
    lock = threading.Lock()
    lock.acquire()
    lock.release()
    print(f"{os.getpid()}:end", flush=True)
else:
    # Parent process
    print(f"{os.getpid()}:child_pid={child_pid}", flush=True)
    lock.release()
    assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING
    pid, status = os.waitpid(child_pid, 0)
    print(f"{os.getpid()}:done", flush=True)
    sys.exit(os.WEXITSTATUS(status))
