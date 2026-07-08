"""
Tracer-agnostic reproducer attempt for the periodic-thread registry corruption.

WIP — this version does not yet reliably reproduce the deadlock.

The core difficulty: for the buggy dealloc delete (DEL #3) to fire with a
non-NULL ident, the GC must collect the *container* object (NativeWriter or
the _Holder here) *before* running tp_clear on the PeriodicThread itself.
If tp_clear runs first it nulls the ident and the delete is skipped safely.
Getting that ordering without the full tracer's allocation graph is unreliable.

See repro_fork_registry_corruption.py for the stable tracer-based reproducer.

Usage
-----
    python tests/internal/repro_fork_no_tracer.py
"""

import gc
import os
import signal
import sys
import time

from ddtrace.internal.periodic import PeriodicThread
from ddtrace.internal._threads import periodic_threads

NFORKS = int(os.environ.get("NFORKS", "25"))
DEADLINE = float(os.environ.get("DEADLINE", "3.0"))
POOL_SIZE = int(os.environ.get("POOL_SIZE", "4"))


class _Holder:
    """Closes the reference cycle that makes old PeriodicThread objects
    GC-collectible.

    Cycle: PeriodicThread._target -> _Holder.work (bound method)
           -> _Holder._thread -> PeriodicThread
    """

    def __init__(self):
        self._thread = None

    def work(self) -> None:
        pass


def _make_thread(name: str) -> PeriodicThread:
    h = _Holder()
    t = PeriodicThread(60.0, h.work, name=name)
    h._thread = t
    t.start()
    return t


def _one_fork(pool: list, idx: int):
    old = pool[idx % len(pool)]
    try:
        old.stop()
    except Exception:
        pass
    old.join(timeout=1.0)

    new_t = _make_thread("churn-%d" % idx)
    pool[idx % len(pool)] = new_t

    del old
    gc.collect()

    pid = os.fork()
    if pid == 0:
        os._exit(0)

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
            return None
        time.sleep(0.02)


def main() -> int:
    pool = [_make_thread("pool-%d" % i) for i in range(POOL_SIZE)]
    time.sleep(0.2)

    n = len(periodic_threads)
    print("pool size: %d  (periodic_threads: %d)" % (POOL_SIZE, n), flush=True)

    print("Running %d forks with %.1fs deadline each..." % (NFORKS, DEADLINE),
          flush=True)

    hung = 0
    for i in range(NFORKS):
        status = _one_fork(pool, i)
        if status is None:
            print("  fork %2d: HUNG ❌" % i, flush=True)
            hung += 1
            if hung >= 3:
                break
        elif not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
            print("  fork %2d: CRASHED ❌" % i, flush=True)
            hung += 1
        else:
            print("  fork %2d: ok ✓" % i, flush=True)

    for t in pool:
        try:
            t.stop()
        except Exception:
            pass

    print()
    if hung:
        print("REPRODUCED: %d fork(s) hung/crashed" % hung)
        return 1
    print("PASS: all %d forks completed" % NFORKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
