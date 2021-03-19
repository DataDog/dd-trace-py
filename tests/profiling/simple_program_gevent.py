from gevent import monkey


monkey.patch_all()

import threading

from ddtrace.profiling import bootstrap
# do not use ddtrace-run; the monkey-patching would be done too late
import ddtrace.profiling.auto
from ddtrace.profiling.collector import stack


def fibonacci(n):
    if n == 0:
        return 0
    elif n == 1:
        return 1
    else:
        return fibonacci(n - 1) + fibonacci(n - 2)


# When not using our special PeriodicThread based on real threads, there's 0 event captured.
i = 1
while len(bootstrap.profiler._profiler._recorder.events[stack.StackSampleEvent]) < 10:
    threads = []
    for _ in range(10):
        t = threading.Thread(target=fibonacci, args=(i,))
        t.start()
        threads.append(t)
    i += 1
    for t in threads:
        t.join()
