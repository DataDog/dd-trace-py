from gevent import monkey


monkey.patch_all()

import threading
import time

from ddtrace.profiling import bootstrap
import ddtrace.profiling.auto
from ddtrace.profiling.collector import stack_event


def fibonacci(n):
    if n == 0:
        return 0
    elif n == 1:
        return 1
    else:
        return fibonacci(n - 1) + fibonacci(n - 2)


# When not using our special PeriodicThread based on real threads, there's 0 event captured.
i = 1
for _ in range(50):
    if len(bootstrap.profiler._profiler._recorder.events[stack_event.StackSampleEvent]) >= 10:
        break
    threads = []
    for _ in range(10):
        t = threading.Thread(target=fibonacci, args=(i,))
        t.start()
        threads.append(t)
    i += 1
    for t in threads:
        t.join()
    time.sleep(0.1)
else:
    assert False, "Not enough events captured"
