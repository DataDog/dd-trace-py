"""
Reproducer for the periodic-thread registry corruption that causes an
infinite join() deadlock in forked children.

Root cause (fixed in this branch):
  Entries in the ``periodic_threads`` dict were deleted by ident without
  checking that the entry still belonged to the deleting thread. Thread IDs
  are recyclable, so the following sequence corrupts the registry:

  1. PeriodicThread A exits → DEL #1 removes ``periodic_threads[100]``.
  2. New PeriodicThread B starts, OS recycles id=100 →
     ``periodic_threads[100] = B`` (SET #1 in the C++ thread lambda).
  3. GC collects the old Python object for A.
     ``tp_dealloc`` fires ``PyDict_DelItem(periodic_threads, A.ident=100)``
     — but ``periodic_threads[100]`` is now B → B is evicted.
  4. ``_before_fork()`` misses B → never stops/joins it → B's ``_stopped``
     is never set.
  5. Child: the at-fork hooks restart the writer, which calls
     ``PeriodicThread.join(None)`` on B → ``_stopped->wait()`` forever.

Why this test uses the tracer
-----------------------------
The dealloc delete fires when GC collects the old PeriodicThread via a
reference cycle (made visible by ``Py_TPFLAGS_HAVE_GC`` in PR #18363). For
DEL #3 to run with a *non-NULL ident*, the container object (NativeWriter)
must be cleared by GC before the PeriodicThread's own ``tp_clear`` runs.

Getting that ordering reliably requires the tracer's rich allocation graph.
With only a handful of directly-created threads the GC tends to clear the
PeriodicThread first, which nulls the ident and skips the delete harmlessly.

Usage
-----
Run directly (shows per-fork status):

    python tests/internal/repro_fork_registry_corruption.py

Override defaults:

    NFORKS=50 DEADLINE=5.0 python tests/internal/repro_fork_registry_corruption.py

Expected output WITHOUT the fix:

    periodic_threads running: 5
    Running 25 forks with 3.0s deadline each...
      fork  0: HUNG (deadlock in _child_after_fork → join(None)) ❌
      ...
    REPRODUCED: 3 fork(s) hung/crashed

Expected output WITH the fix:

    periodic_threads running: 5
    Running 25 forks with 3.0s deadline each...
      fork  0: ok ✓  ...  fork 24: ok ✓
    PASS: all 25 forks completed within 3.0s deadline
"""

import gc
import os
import signal
import sys
import time

import ddtrace.auto  # noqa: F401 — starts tracer + all periodic threads
from ddtrace.internal._threads import periodic_threads
from ddtrace.trace import tracer

NFORKS = int(os.environ.get("NFORKS", "25"))
DEADLINE = float(os.environ.get("DEADLINE", "3.0"))


def _one_fork(idx: int):
    """Run one fork iteration.

    Returns the child's waitpid status word on normal exit, or None if hung.
    """
    # Churn the writer: old worker exits (freeing its thread id) and a new
    # one starts (potentially recycling it). The old NativeWriter object
    # forms a reference cycle that GC can collect, triggering the buggy
    # dealloc delete.
    agg = tracer._span_aggregator
    agg.writer = agg.writer.recreate()

    with tracer.trace("pre-%d" % idx):
        pass

    # Force collection: if the old NativeWriter cycle is collected and the
    # new worker recycled the freed thread id, tp_dealloc's blind
    # PyDict_DelItem evicts the live new worker from periodic_threads.
    gc.collect()

    pid = os.fork()
    if pid == 0:
        # Child: _child_after_fork already ran via the at-fork hooks.
        # If the bug is present the child deadlocks there and never reaches here.
        os._exit(0)

    # Parent: signal-free deadline watchdog.
    deadline = time.monotonic() + DEADLINE
    while True:
        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return 0
        if wpid == pid:
            return status
        if time.monotonic() > deadline:
            try:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
            except (ChildProcessError, ProcessLookupError):
                pass
            return None  # hung
        time.sleep(0.02)


def main() -> int:
    # Warm up: emit a span so the writer and all periodic threads are running.
    with tracer.trace("warmup"):
        pass
    time.sleep(1.0)

    n = len(periodic_threads)
    print("periodic_threads running: %d" % n, flush=True)
    if n < 3:
        print("SKIP: too few periodic threads — recycled-ident path not exercised")
        return 0

    print("Running %d forks with %.1fs deadline each..." % (NFORKS, DEADLINE),
          flush=True)

    hung = 0
    for i in range(NFORKS):
        status = _one_fork(i)
        if status is None:
            print("  fork %2d: HUNG (deadlock in _child_after_fork -> join(None)) ❌"
                  % i, flush=True)
            hung += 1
            if hung >= 3:
                break
        elif not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
            sig = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
            print("  fork %2d: CRASHED (status=%d sig=%s) ❌" % (i, status, sig),
                  flush=True)
            hung += 1
        else:
            print("  fork %2d: ok ✓" % i, flush=True)

    print()
    if hung:
        print("REPRODUCED: %d fork(s) hung/crashed — registry corruption confirmed"
              % hung)
        return 1

    print("PASS: all %d forks completed within %.1fs deadline" % (NFORKS, DEADLINE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
