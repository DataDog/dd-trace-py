import os
import sys
import threading

from ddtrace.internal import service
import ddtrace.profiling.auto
import ddtrace.profiling.bootstrap
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading as cthreading
import ddtrace.profiling.profiler


lock = threading.Lock()
lock.acquire()
test_lock_name = "simple_program_fork.py:13"


assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING

parent_recorder = ddtrace.profiling.bootstrap.profiler._profiler._recorder

child_pid = os.fork()
if child_pid == 0:
    # Child
    # This is the first thing done on Python 3.7 and later, so mimick it here
    if sys.version_info[:2] < (3, 7):
        ddtrace.profiling.auto.start_profiler()

    recorder = ddtrace.profiling.bootstrap.profiler._profiler._recorder

    assert recorder is not parent_recorder

    # Release it
    lock.release()

    # We don't track it
    assert test_lock_name not in set(e.lock_name for e in recorder.reset()[cthreading.LockReleaseEvent])

    # We track this one though
    lock = threading.Lock()
    test_lock_name = "simple_program_fork.py:40"
    assert test_lock_name not in set(e.lock_name for e in recorder.reset()[cthreading.LockAcquireEvent])
    lock.acquire()
    events = recorder.reset()
    assert test_lock_name in set(e.lock_name for e in events[cthreading.LockAcquireEvent])
    assert test_lock_name not in set(e.lock_name for e in events[cthreading.LockReleaseEvent])
    lock.release()
    assert test_lock_name in set(e.lock_name for e in recorder.reset()[cthreading.LockReleaseEvent])

    parent_events = parent_recorder.reset()
    # Let's sure our copy of the parent recorder does not receive it since the parent profiler has been stopped
    assert test_lock_name not in set(e.lock_name for e in parent_events[cthreading.LockAcquireEvent])
    assert test_lock_name not in set(e.lock_name for e in parent_events[cthreading.LockReleaseEvent])

    # This can run forever if anything is broken!
    while not recorder.events[stack.StackSampleEvent]:
        pass
else:
    recorder = ddtrace.profiling.bootstrap.profiler._profiler._recorder
    assert recorder is parent_recorder
    assert test_lock_name not in set(e.lock_name for e in recorder.reset()[cthreading.LockReleaseEvent])
    lock.release()
    assert test_lock_name in set(e.lock_name for e in recorder.reset()[cthreading.LockReleaseEvent])
    assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING
    print(child_pid)
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
