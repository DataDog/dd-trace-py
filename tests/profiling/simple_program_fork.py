import os
import sys
import threading

import ddtrace.profiling.auto
import ddtrace.profiling.bootstrap
import ddtrace.profiling.profiler
from ddtrace.profiling.collector import stack
from ddtrace.profiling.collector import threading as cthreading


lock = threading.Lock()
lock.acquire()
test_lock_name = "simple_program_fork.py:12"


assert ddtrace.profiling.bootstrap.profiler.status == ddtrace.profiling.profiler.ProfilerStatus.RUNNING

parent_recorder = list(ddtrace.profiling.bootstrap.profiler.recorders)[0]

child_pid = os.fork()
if child_pid == 0:
    # Child
    # This is the first thing done on Python 3.7 and later, so mimick it here
    if sys.version_info[:2] < (3, 7):
        ddtrace.profiling.auto.start_profiler()

    recorder = list(ddtrace.profiling.bootstrap.profiler.recorders)[0]

    assert recorder is not parent_recorder

    # Release it
    lock.release()

    # We don't track it
    assert test_lock_name not in set(e.lock_name for e in recorder.events[cthreading.LockReleaseEvent])

    # We track this one though
    lock = threading.Lock()
    test_lock_name = "simple_program_fork.py:39"
    assert test_lock_name not in set(e.lock_name for e in recorder.events[cthreading.LockAcquireEvent])
    lock.acquire()
    assert test_lock_name in set(e.lock_name for e in recorder.events[cthreading.LockAcquireEvent])
    assert test_lock_name not in set(e.lock_name for e in recorder.events[cthreading.LockReleaseEvent])
    lock.release()
    assert test_lock_name in set(e.lock_name for e in recorder.events[cthreading.LockReleaseEvent])

    # Let's sure our copy of the parent recorder does not receive it since the parent profiler has been stopped
    assert test_lock_name not in set(e.lock_name for e in parent_recorder.events[cthreading.LockAcquireEvent])
    assert test_lock_name not in set(e.lock_name for e in parent_recorder.events[cthreading.LockReleaseEvent])

    # This can run forever if anything is broken!
    while not recorder.events[stack.StackSampleEvent]:
        pass
    assert recorder.events[cthreading.LockAcquireEvent]
else:
    recorder = list(ddtrace.profiling.bootstrap.profiler.recorders)[0]
    assert recorder is parent_recorder
    assert test_lock_name not in set(e.lock_name for e in recorder.events[cthreading.LockReleaseEvent])
    lock.release()
    assert test_lock_name in set(e.lock_name for e in recorder.events[cthreading.LockReleaseEvent])
    assert ddtrace.profiling.bootstrap.profiler.status == ddtrace.profiling.profiler.ProfilerStatus.RUNNING
    print(child_pid)
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
