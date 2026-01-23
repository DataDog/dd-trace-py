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


def _stop_profiler_with_timeout(timeout_s: float = 5.0) -> None:
    done = []

    def _stop() -> None:
        ddtrace.profiling.bootstrap.profiler._profiler.stop(flush=True, join=False)
        done.append(True)

    stopper = threading.Thread(target=_stop, name="profiler-stop", daemon=True)
    stopper.start()
    stopper.join(timeout_s)
    if not done:
        faulthandler.dump_traceback(all_threads=True)


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
    _stop_profiler_with_timeout()
    faulthandler.cancel_dump_traceback_later()
    os._exit(0)
else:
    lock.release()
    # pyright: ignore[reportAttributeAccessIssue]
    assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING
    print(child_pid)
    pid, status = os.waitpid(child_pid, 0)
    # Avoid hanging on profiler shutdown in parent after wait.
    _stop_profiler_with_timeout()
    faulthandler.cancel_dump_traceback_later()
    sys.exit(os.WEXITSTATUS(status))
