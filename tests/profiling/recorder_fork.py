import os
import sys

from ddtrace.profiling import Profiler
from ddtrace.profiling.collector import stack


p = Profiler()
p.start(profile_children=False, stop_on_exit=False)


e = stack.StackSampleEvent()

p._profiler._recorder.push_event(e)
assert e in list(p._profiler._recorder.reset()[stack.StackSampleEvent])
assert e not in list(p._profiler._recorder.reset()[stack.StackSampleEvent])

pid = os.fork()

if pid == 0:
    e = stack.StackSampleEvent()
    p._profiler._recorder.push_event(e)
    assert list(p._profiler._recorder.reset()[stack.StackSampleEvent]) == []
    assert list(p._profiler._recorder.reset()[stack.StackSampleEvent]) == []
else:
    e = stack.StackSampleEvent()
    p._profiler._recorder.push_event(e)
    assert e in list(p._profiler._recorder.reset()[stack.StackSampleEvent])
    assert e not in list(p._profiler._recorder.reset()[stack.StackSampleEvent])
    p.stop(flush=False)
    pid, status = os.waitpid(pid, 0)
    sys.exit(os.WEXITSTATUS(status))
