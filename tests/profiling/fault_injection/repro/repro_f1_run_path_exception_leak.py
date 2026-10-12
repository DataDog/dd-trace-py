"""F1: exception in periodic work strands the Scheduler worker.

Finding: after stop() returns, a ddtrace.profiling.scheduler:Scheduler
PeriodicThread is still registered/alive in the native registry.
Fault points: --point memalloc (MemoryCollector.snapshot — the suite's first
observed leaker) or --point upload (ddup.upload).
See INVESTIGATION.md F1. Exit 1 if reproduced.
"""
import sys
import threading
import time

import _common


def run_with_memalloc_snapshot_fault():
    from ddtrace.profiling.collector import memalloc as memalloc_mod

    cls = memalloc_mod.MemoryCollector
    orig = cls.__dict__["snapshot"]  # descriptor-safe restore
    calls = {"n": 0}

    def raising_snapshot(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("injected: MemoryCollector.snapshot (F1)")

    cls.snapshot = staticmethod(raising_snapshot)
    return cls, "snapshot", orig, calls


def run_with_upload_fault():
    from ddtrace.internal.datadog.profiling import ddup

    calls = {"n": 0}
    orig = ddup.upload

    def raising_upload(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("injected: ddup.upload (F1)")

    ddup.upload = raising_upload
    return ddup, "upload", orig, calls


def main(point="memalloc"):
    if len(sys.argv) > 2 and sys.argv[1] == "--point":
        point = sys.argv[2]
    if point == "memalloc":
        setup = run_with_memalloc_snapshot_fault
    elif point == "upload":
        setup = run_with_upload_fault
    else:
        raise SystemExit(f"unknown point {point!r}")

    target, attr, orig, calls = setup()
    try:
        p = _common.make_profiler()
        p.start()
        lock = threading.Lock()
        deadline = time.monotonic() + 1.0
        acc = 0
        while time.monotonic() < deadline:
            with lock:
                acc = (acc * 31 + 1) % 1000003
        print(f"[F1:{point}] fault fired {calls['n']} time(s) during run", flush=True)
        if calls["n"] == 0:
            print(f"[F1:{point}] NOT REPRODUCED: fault never fired", flush=True)
            _common.stop_guarded(p, f"F1:{point}")
            return 0
        _common.stop_guarded(p, f"F1:{point}")
        time.sleep(0.5)
        leftover = _common.profiler_threads()
        for t in leftover:
            print(f"[F1:{point}] leftover profiler thread: {getattr(t, 'name', t)!r}", flush=True)
        if leftover:
            print(f"[F1:{point}] REPRODUCED: run-path exception strands scheduler thread(s) after stop()", flush=True)
            return 1
        print(f"[F1:{point}] NOT REPRODUCED: no leftover profiler threads", flush=True)
        return 0
    finally:
        setattr(target, attr, orig)


if __name__ == "__main__":
    sys.exit(main())
