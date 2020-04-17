from gevent import monkey

monkey.patch_all()

import threading

# do not use pyddprofile; the monkey-patching would be done too late
import ddtrace.profiling.auto
from ddtrace.profiling import bootstrap
from ddtrace.profiling.collector import stack


def fibonacci(n):
    if n == 0:
        return 0
    elif n == 1:
        return 1
    else:
        return fibonacci(n - 1) + fibonacci(n - 2)


threads = []
for x in range(10):
    t = threading.Thread(target=fibonacci, args=(32,))
    t.start()
    threads.append(t)


for t in threads:
    t.join()


recorder = bootstrap.profiler.recorders.pop()
# When not using our special PeriodicThread based on real threads, there's 0 event captured.
assert len(recorder.events[stack.StackSampleEvent]) > 10
