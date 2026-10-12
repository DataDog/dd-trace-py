"""F8: leaked threads pollute every later leak check (cross-test isolation).

Cycle 1 injects the F1 fault (upload raises) and strands a scheduler thread.
Cycle 2 runs a completely CLEAN profiler start/stop in the same process -
its leak oracle still reports the stale thread(s) from cycle 1, because
ddtrace.internal._threads.periodic_threads is process-global.
Exit 1 if the clean cycle inherits leaked threads.
"""
import sys
import threading
import time

import _common


def churn(seconds):
    lock = threading.Lock()
    deadline = time.monotonic() + seconds
    acc = 0
    while time.monotonic() < deadline:
        with lock:
            acc = (acc * 31 + 1) % 1000003


def main():
    from ddtrace.internal.datadog.profiling import ddup

    orig = ddup.upload

    def raising_upload(*a, **kw):
        raise RuntimeError("injected: ddup.upload (F8 cycle 1)")

    # cycle 1: faulted
    ddup.upload = raising_upload
    p1 = _common.make_profiler()
    p1.start()
    churn(1.0)
    _common.stop_guarded(p1, "F8")
    time.sleep(0.5)
    leaked1 = _common.profiler_threads()
    print(f"[F8] cycle 1 (faulted) leftovers: {[str(getattr(t, 'name', t)) for t in leaked1]}", flush=True)

    # cycle 2: clean
    ddup.upload = orig
    p2 = _common.make_profiler()
    p2.start()
    churn(0.5)
    _common.stop_guarded(p2, "F8")
    time.sleep(0.5)
    leaked2 = _common.profiler_threads()
    print(f"[F8] cycle 2 (clean) leftovers:   {[str(getattr(t, 'name', t)) for t in leaked2]}", flush=True)

    if leaked2:
        print("[F8] REPRODUCED: clean profiler cycle inherits leaked threads from the faulted cycle (registry pollution)", flush=True)
        return 1
    print("[F8] NOT REPRODUCED: clean cycle reports no leftovers", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
