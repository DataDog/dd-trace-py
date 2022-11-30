import os
import sys

from ddtrace.profiling import Profiler
from ddtrace.profiling.collector import stack_event


p = Profiler()
p.start(profile_children=False, stop_on_exit=False)


e = stack_event.StackSampleEvent()

p._profiler._recorder.push_event(e)
assert e in list(p._profiler._recorder.reset()[stack_event.StackSampleEvent])
assert e not in list(p._profiler._recorder.reset()[stack_event.StackSampleEvent])

pid = os.fork()

if pid == 0:
    e = stack_event.StackSampleEvent()
    p._profiler._recorder.push_event(e)
    assert list(p._profiler._recorder.reset()[stack_event.StackSampleEvent]) == []
    assert list(p._profiler._recorder.reset()[stack_event.StackSampleEvent]) == []
else:
    e = stack_event.StackSampleEvent()
    p._profiler._recorder.push_event(e)
    assert e in list(p._profiler._recorder.reset()[stack_event.StackSampleEvent])
    assert e not in list(p._profiler._recorder.reset()[stack_event.StackSampleEvent])
    p.stop(flush=False)
    pid, status = os.waitpid(pid, 0)
    sys.exit(os.WEXITSTATUS(status))
