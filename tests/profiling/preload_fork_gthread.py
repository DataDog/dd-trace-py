"""
Regression script: profiler starts before fork, worker creates threads after fork.

Simulates:
  - Admission-injection / sitecustomize startup (profiler starts before fork)
  - Gunicorn preload_app=True (app loaded in master, workers forked)
  - gthread worker class (multiple threads handle requests inside each worker)

The parent prints the child PID so the test can locate the child's pprof files.
"""

import os
import sys
import threading
import time

# Simulate admission injection: profiler starts in the master process before fork.
import ddtrace.profiling.auto  # noqa: F401


def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)


def worker_task() -> None:
    """CPU-bound work so the sampler captures stack frames on this thread."""
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        fib(25)


child_pid = os.fork()

if child_pid == 0:
    # Worker process: spin up threads like Gunicorn's gthread worker does.
    threads = [threading.Thread(target=worker_task, name=f"WorkerThread-{i}") for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Clean exit triggers atexit → profiler.stop(flush=True) → pprof written to disk.
    sys.exit(0)
else:
    # Master process: print child PID for the test, then wait.
    print(child_pid)
    _, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
