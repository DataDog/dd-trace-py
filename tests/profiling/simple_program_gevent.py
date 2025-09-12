# Import from ddtrace before monkey patching to ensure that we grab all the
# necessary references to the unpatched modules.
import ddtrace.auto  # noqa: F401, I001
import ddtrace.profiling.auto  # noqa:F401


import gevent.monkey # noqa:F402

gevent.monkey.patch_all()

import threading  # noqa: E402, F402, I001
import time  # noqa: E402, F402


def fibonacci(n):
    if n == 0:
        return 0
    elif n == 1:
        return 1
    else:
        return fibonacci(n - 1) + fibonacci(n - 2)


i = 1
for _ in range(20):
    threads = []
    for _ in range(10):
        t = threading.Thread(target=fibonacci, args=(i,))
        t.start()
        threads.append(t)
    i += 1
    for t in threads:
        t.join()
    time.sleep(0.1)
