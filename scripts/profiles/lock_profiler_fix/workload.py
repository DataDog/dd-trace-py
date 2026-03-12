"""Lock profiler workload -- runs under ddtrace-run.

By the time this file executes:
  1. sitecustomize.py has loaded ddtrace and started the profiler
  2. The profiler has patched threading.Lock, threading.RLock, etc.
  3. cleanup_loaded_modules() has removed threading from sys.modules
     (because DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE=1)
  4. This 'import threading' gets a FRESH, re-imported module

On main (broken): threading.Lock() returns a native _thread.lock (not profiled).
On fix branch:    threading.Lock() returns a _ProfiledLock (profiled).
"""

import os
import sys
import threading
import time


lock = threading.Lock()
lock_type = f"{type(lock).__module__}.{type(lock).__qualname__}"
print(f"Lock type: {lock_type}", file=sys.stderr)

for _ in range(200):
    lock.acquire()
    time.sleep(0.001)
    lock.release()

# Let the profiler scheduler flush at least one profile
time.sleep(3)

print(os.getpid())
