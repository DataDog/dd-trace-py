"""F4: class-level patch of StackCollector.snapshot never fires during a run.

Symptom from the suite: arming a fault on StackCollector.snapshot records 0
fires while the same harness fires on memalloc.snapshot / ddup.upload.
This repro counts class-level snapshot calls + upload calls as control, and
dumps diagnostics on how the scheduler/collector invoke snapshot.
Exit 1 if the arm-miss symptom reproduces (upload fired, snapshot did not).
"""
import sys
import threading
import time

import _common


def main():
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack as stack_mod

    counters = {"snapshot": 0, "upload": 0}
    orig_snap = stack_mod.StackCollector.snapshot
    orig_upload = ddup.upload

    def counting_snapshot(self, *a, **kw):
        counters["snapshot"] += 1
        return orig_snap(self, *a, **kw)

    def counting_upload(*a, **kw):
        counters["upload"] += 1
        return orig_upload(*a, **kw)

    stack_mod.StackCollector.snapshot = counting_snapshot
    ddup.upload = counting_upload
    try:
        p = _common.make_profiler()
        p.start()

        # generate python frames for the stack sampler
        def workload():
            def fib(n):
                return n if n < 2 else fib(n - 1) + fib(n - 2)

            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                fib(18)

        threads = [threading.Thread(target=workload, daemon=True) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(2)

        _common.stop_guarded(p, "F4")
        print(f"[F4] StackCollector.snapshot class-attr calls: {counters['snapshot']}", flush=True)
        print(f"[F4] ddup.upload calls (control):               {counters['upload']}", flush=True)

        # diagnostics for the investigator
        for c in p._profiler._collectors:
            klass = type(c)
            print(
                f"[F4] collector {klass.__name__}: "
                f"snapshot-on-class={('snapshot' in vars(klass))!r:5} "
                f"snapshot-on-instance={('snapshot' in vars(c))!r:5} "
                f"periodic={hasattr(klass, 'periodic')!r}",
                flush=True,
            )
        if counters["upload"] >= 1 and counters["snapshot"] == 0:
            print("[F4] REPRODUCED: class-level StackCollector.snapshot patch is never invoked", flush=True)
            return 1
        print("[F4] NOT REPRODUCED (snapshot fired or control did not)", flush=True)
        return 0
    finally:
        stack_mod.StackCollector.snapshot = orig_snap
        ddup.upload = orig_upload


if __name__ == "__main__":
    sys.exit(main())
