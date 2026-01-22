import os
import sys
import threading

from ddtrace.internal import service
import ddtrace.profiling.auto  # noqa: F401
import ddtrace.profiling.bootstrap
import ddtrace.profiling.profiler  # noqa: F401


lock = threading.Lock()
lock.acquire()


# pyright: ignore[reportAttributeAccessIssue]
assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING


child_pid = os.fork()
if child_pid == 0:
    # Release it
    lock.release()

    # We track this one though
    lock = threading.Lock()
    lock.acquire()
    lock.release()
    # Avoid hanging on profiler shutdown in child after fork.
    ddtrace.profiling.bootstrap.profiler._profiler.stop(flush=True, join=False)
    os._exit(0)
else:
    lock.release()
    # pyright: ignore[reportAttributeAccessIssue]
    assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING
    print(child_pid)
    pid, status = os.waitpid(child_pid, 0)
    # Avoid hanging on profiler shutdown in parent after wait.
    ddtrace.profiling.bootstrap.profiler._profiler.stop(flush=True, join=False)
    sys.exit(os.WEXITSTATUS(status))
