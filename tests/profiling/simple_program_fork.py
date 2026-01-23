import faulthandler
import os
import sys
import threading


os.environ.setdefault("DD_PROFILING_API_TIMEOUT_MS", "1000")
faulthandler.dump_traceback_later(120, repeat=True)

from ddtrace.internal import service  # noqa: E402
import ddtrace.profiling.auto  # noqa: F401,E402
import ddtrace.profiling.bootstrap  # noqa: E402
import ddtrace.profiling.profiler  # noqa: F401,E402


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
    faulthandler.cancel_dump_traceback_later()
    os._exit(0)
else:
    lock.release()
    # pyright: ignore[reportAttributeAccessIssue]
    assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING
    print(child_pid)
    pid, status = os.waitpid(child_pid, 0)
    # Avoid hanging on profiler shutdown in parent after wait.
    ddtrace.profiling.bootstrap.profiler._profiler.stop(flush=True, join=False)
    faulthandler.cancel_dump_traceback_later()
    sys.exit(os.WEXITSTATUS(status))
