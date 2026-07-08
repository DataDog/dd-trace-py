"""
Standalone reproducer for the periodic-thread registry corruption that causes
an infinite join() deadlock in forked children.

Root cause (fixed in PR #18913):
  Entries in the ``periodic_threads`` dict were deleted by ident without
  checking that the entry still belonged to the deleting thread. Thread IDs are
  recyclable, so the following sequence corrupts the registry and deadlocks a
  fork child:

  1. ``writer.recreate()`` cycles: old worker P exits, freeing thread id=100,
     removing ``periodic_threads[100]``.
  2. New worker Q starts. The OS recycles id=100; Q registers
     ``periodic_threads[100] = Q``.
  3. Cyclic GC (enabled by PR #18363's ``Py_TPFLAGS_HAVE_GC``) later collects
     the dropped old-writer <-> old-worker reference cycle. The old worker P's
     ``tp_dealloc`` runs ``PyDict_DelItem(periodic_threads, P.ident=100)`` —
     but ``periodic_threads[100]`` is now Q (a live worker). Q is evicted.
  4. ``_before_fork()`` snapshots ``periodic_threads``. Q is absent; it is
     never stopped/joined before the fork. Its ``_stopped`` event is never set.
  5. In the child, ``Tracer._child_after_fork`` recreates the writer, which
     calls ``PeriodicThread.join(None)`` on Q — waiting forever on a condition
     variable nothing will ever signal. The child hangs.

The fix (PR #18913): guard every by-ident delete with an identity check so a
stale thread can only remove its own registry entry, never a live worker that
recycled the same id.

Usage
-----
Run directly (shows per-fork status):

    python tests/internal/repro_fork_registry_corruption.py

Override defaults via env vars:

    NFORKS=50 DEADLINE=5.0 python tests/internal/repro_fork_registry_corruption.py

Expected output WITHOUT the fix (current _threads.cpp):

    periodic_threads running: 5
    Running 25 forks with 3.0s deadline each...
      fork  0: HUNG (deadlock in _child_after_fork → join(None)) ❌
      fork  1: HUNG ...
      ...
    REPRODUCED: 3 fork(s) hung/crashed — registry corruption confirmed

Expected output WITH the fix (PR #18913):

    periodic_threads running: 5
    Running 25 forks with 3.0s deadline each...
      fork  0: ok ✓
      ...
    PASS: all 25 forks completed within 3.0s deadline
"""

import gc
import os
import sys
import time

os.environ.setdefault("DD_TRACE_STARTUP_LOGS", "0")

import ddtrace.auto  # noqa: F401 — starts tracer + all periodic threads
from ddtrace.internal._threads import periodic_threads
from ddtrace.trace import tracer

NFORKS = int(os.environ.get("NFORKS", "25"))
DEADLINE = float(os.environ.get("DEADLINE", "3.0"))


def _one_fork(idx: int):
    """Run one fork iteration.

    Returns the child's waitpid status word on normal exit, or None if the
    child hung past the deadline (killed with SIGKILL by the parent).
    """
    # Churn the writer so an old-worker reference cycle is dropped and the OS
    # can recycle the freed thread id for the replacement worker.
    agg = tracer._span_aggregator
    agg.writer = agg.writer.recreate()

    with tracer.trace("pre-%d" % idx):
        pass

    # Force collection: if a dropped old-worker cycle exists and the new worker
    # recycled the same thread id, the blind PyDict_DelItem in tp_dealloc will
    # evict the live new worker from periodic_threads.
    gc.collect()

    pid = os.fork()
    if pid == 0:
        # Child: Tracer._child_after_fork already ran in the at-fork hooks.
        # If the bug is present the child deadlocks there and never reaches here.
        try:
            with tracer.trace("child-%d" % idx):
                pass
        finally:
            os._exit(0)

    # Parent: signal-free deadline watchdog.
    deadline = time.monotonic() + DEADLINE
    while True:
        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return 0  # already reaped — treat as clean exit
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


def main():
    # Warm up: emit a span so the writer and all periodic threads are running.
    with tracer.trace("warmup"):
        pass
    time.sleep(1.0)

    n = len(periodic_threads)
    print("periodic_threads running: %d" % n, flush=True)
    if n < 2:
        print("SKIP: too few periodic threads — recycled-ident path not exercised")
        return 0

    print("Running %d forks with %.1fs deadline each..." % (NFORKS, DEADLINE), flush=True)

    hung = 0
    for i in range(NFORKS):
        status = _one_fork(i)
        if status is None:
            print("  fork %2d: HUNG (deadlock in _child_after_fork -> join(None)) ❌" % i,
                  flush=True)
            hung += 1
            if hung >= 3:
                # Bail early: enough evidence, no need to wait for all forks.
                break
        elif not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
            sig = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
            print("  fork %2d: CRASHED (status=%d sig=%s) ❌" % (i, status, sig), flush=True)
            hung += 1
        else:
            print("  fork %2d: ok ✓" % i, flush=True)

    print()
    if hung:
        print("REPRODUCED: %d fork(s) hung/crashed — registry corruption confirmed" % hung)
        return 1

    print("PASS: all %d forks completed within %.1fs deadline" % (NFORKS, DEADLINE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
