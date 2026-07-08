"""
Tracer-agnostic reproducer for periodic-thread registry corruption.

This does not need the tracer/NativeWriter object graph. It reproduces the
underlying registry bug directly:

  1. PeriodicThread A exits and removes periodic_threads[tid].
  2. PeriodicThread B starts and the OS may recycle the same tid, registering
     periodic_threads[tid] = B.
  3. A NativeWriter-like container teardown drops the last reference to A while
     A.ident is still non-NULL.
  4. On buggy builds, PeriodicThread.tp_dealloc blindly deletes by ident and
     evicts B from periodic_threads.

The tracer-based reproducer demonstrates the fork deadlock consequence. This
script reports the registry corruption directly, which is more reliable without
pulling in tracer internals.

Usage
-----
    python tests/internal/repro_fork_no_tracer.py

Useful knobs:
    NFORKS=1000 POOL_SIZE=1 python tests/internal/repro_fork_no_tracer.py
"""

import gc
import os
import sys
import time

from ddtrace.internal._threads import periodic_threads
from ddtrace.internal.periodic import PeriodicThread


NFORKS = int(os.environ.get("NFORKS", "1000"))
DEADLINE = float(os.environ.get("DEADLINE", "3.0"))
POOL_SIZE = int(os.environ.get("POOL_SIZE", "1"))
DO_FORK = os.environ.get("DO_FORK", "0") == "1"


class _Holder:
    """NativeWriter-like owner for a PeriodicThread.

    The important bit is that teardown can drop the holder -> thread edge before
    PeriodicThread.tp_clear has cleared ``thread.ident``. NativeWriter has a more
    complex object graph that naturally hits this ordering; this minimal holder
    lets the reproducer exercise the same native dealloc path directly.
    """

    def __init__(self, name: str) -> None:
        self.thread = PeriodicThread(60.0, self.work, name=name)
        self.thread.start()

    def work(self) -> None:
        pass


def _make_holder(name: str) -> _Holder:
    return _Holder(name)


def _one_iteration(pool: list[_Holder], idx: int):
    old_holder = pool[idx % len(pool)]
    old_thread = old_holder.thread
    old_ident = old_thread.ident

    try:
        old_thread.stop()
    except Exception:
        pass
    old_thread.join(timeout=1.0)

    new_holder = _make_holder("churn-%d" % idx)
    new_thread = new_holder.thread
    new_ident = new_thread.ident
    pool[idx % len(pool)] = new_holder

    # Simulate container teardown after replacement: drop the old owner ->
    # PeriodicThread edge while old_thread.ident is still populated. If the OS
    # recycled old_ident for new_thread, buggy tp_dealloc deletes the live
    # new_thread entry from periodic_threads.
    old_holder.thread = None
    del old_thread
    gc.collect()

    if old_ident == new_ident and periodic_threads.get(new_ident) is not new_thread:
        return "corrupt", old_ident, new_ident

    if not DO_FORK:
        return 0, old_ident, new_ident

    pid = os.fork()
    if pid == 0:
        os._exit(0)

    deadline = time.monotonic() + DEADLINE
    while True:
        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return 0, old_ident, new_ident
        if wpid == pid:
            return status, old_ident, new_ident
        if time.monotonic() > deadline:
            try:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
            except (ChildProcessError, ProcessLookupError):
                pass
            return None, old_ident, new_ident
        time.sleep(0.02)


def main() -> int:
    pool = [_make_holder("pool-%d" % i) for i in range(POOL_SIZE)]
    time.sleep(0.2)

    print("pool size: %d  (periodic_threads: %d)" % (POOL_SIZE, len(periodic_threads)), flush=True)
    print("Running %d iterations with %.1fs deadline each..." % (NFORKS, DEADLINE), flush=True)

    bad = 0
    for i in range(NFORKS):
        status, old_ident, new_ident = _one_iteration(pool, i)
        if status == "corrupt":
            print(
                "  iter %3d: REGISTRY CORRUPTED old_ident=%r new_ident=%r ❌" % (i, old_ident, new_ident),
                flush=True,
            )
            bad += 1
            if bad >= 3:
                break
        elif status is None:
            print("  iter %3d: HUNG ❌" % i, flush=True)
            bad += 1
            if bad >= 3:
                break
        elif not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
            print("  iter %3d: CRASHED ❌" % i, flush=True)
            bad += 1
        elif i < 10 or i % 100 == 0:
            print("  iter %3d: ok ✓" % i, flush=True)

    for holder in pool:
        if holder.thread is not None:
            try:
                holder.thread.stop()
            except Exception:
                pass

    print()
    if bad:
        print("REPRODUCED: %d corrupted registry/hung/crashed iteration(s)" % bad)
        return 1
    print("PASS: all %d iterations completed" % NFORKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
