import os
import sys
import threading

from ddtrace.internal import service
import ddtrace.profiling.auto
import ddtrace.profiling.bootstrap
from ddtrace.profiling.collector import stack_event
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
    # This is the first thing done on Python 3.7 and later, so mimic it here
    if sys.version_info[:2] < (3, 7):
        ddtrace.profiling.auto.start_profiler()

    recorder = ddtrace.profiling.bootstrap.profiler._profiler._recorder

    assert recorder is not parent_recorder

    # Release it
    lock.release()

    # We don't track it
    assert test_lock_name not in set(e.lock_name for e in recorder.reset()[cthreading.ThreadingLockReleaseEvent])

    # We track this one though
    lock = threading.Lock()
    test_lock_name = "simple_program_fork.py:40"
    assert test_lock_name not in set(e.lock_name for e in recorder.reset()[cthreading.ThreadingLockAcquireEvent])
    lock.acquire()
    events = recorder.reset()
    assert test_lock_name in set(e.lock_name for e in events[cthreading.ThreadingLockAcquireEvent])
    assert test_lock_name not in set(e.lock_name for e in events[cthreading.ThreadingLockReleaseEvent])
    lock.release()
    assert test_lock_name in set(e.lock_name for e in recorder.reset()[cthreading.ThreadingLockReleaseEvent])

    parent_events = parent_recorder.reset()
    # Let's sure our copy of the parent recorder does not receive it since the parent profiler has been stopped
    assert test_lock_name not in set(e.lock_name for e in parent_events[cthreading.ThreadingLockAcquireEvent])
    assert test_lock_name not in set(e.lock_name for e in parent_events[cthreading.ThreadingLockReleaseEvent])

    # This can run forever if anything is broken!
    while not recorder.events[stack_event.StackSampleEvent]:
        pass
else:
    recorder = ddtrace.profiling.bootstrap.profiler._profiler._recorder
    assert recorder is parent_recorder
    assert test_lock_name not in set(e.lock_name for e in recorder.reset()[cthreading.ThreadingLockReleaseEvent])
    lock.release()
    assert test_lock_name in set(e.lock_name for e in recorder.reset()[cthreading.ThreadingLockReleaseEvent])
    assert ddtrace.profiling.bootstrap.profiler.status == service.ServiceStatus.RUNNING
    print(child_pid)
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
