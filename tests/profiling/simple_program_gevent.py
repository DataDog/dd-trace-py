from gevent import monkey

# Import from ddtrace before monkey patching to ensure that we grab all the
# necessary references to the unpatched modules.
from ddtrace.profiling import bootstrap
import ddtrace.profiling.auto  # noqa
from ddtrace.profiling.collector import stack_event


monkey.patch_all()

import threading
import time


def fibonacci(n):
    if n == 0:
        return 0
    elif n == 1:
        return 1
    else:
        return fibonacci(n - 1) + fibonacci(n - 2)


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
