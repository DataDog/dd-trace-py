"""F5: lock-collector invariant violation leaks into APPLICATION threads.

Symptom from the suite (test_run_fault_scheduler_thread_dies): workload
threads died with
  AssertionError('Expected acquired_time to be None when acquire is not
  sampled, got <ns timestamp>')
which is ddtrace/profiling/collector/_lock.pyx:218 (assert-gated by
DD_PROFILING_ENABLE_ASSERTS): a *sampled* acquire sets acquired_time
(_lock.pyx:239) and the release path clears it (_lock.pyx:269); a later
unsampled acquire on the same lock trips the assert when the clear was
missed. The exception surfaces inside the app thread's `with lock:`.

This repro kills the scheduler worker (Scheduler.periodic raises -> native
worker dies silently) while a lock-churning workload runs with asserts on.
Exit 1 if the acquired_time AssertionError appears in a workload thread.

NOTE: interaction-dependent (see INVESTIGATION.md F5) - the pytest test
test_run_fault_scheduler_thread_dies is the authoritative repro; this
standalone is best-effort.
"""
import os
import sys
import threading
import time

os.environ.setdefault("DD_TESTING_RAISE", "1")
os.environ.setdefault("DD_PROFILING_ENABLE_ASSERTS", "1")

import _common  # noqa: E402


def main():
    from ddtrace.profiling import scheduler as scheduler_mod

    def dead_periodic(self):  # noqa: ARG001
        raise RuntimeError("injected: Scheduler.periodic (F5)")

    scheduler_mod.Scheduler.periodic = dead_periodic

    p = _common.make_profiler()
    p.start()

    exceptions = []
    barrier = threading.Barrier(3)

    def worker(n):
        lock = threading.Lock()
        try:
            barrier.wait(5)
            deadline = time.monotonic() + 2.0
            acc = 0
            while time.monotonic() < deadline:
                with lock:
                    for i in range(200):
                        acc = (acc * 31 + i) % 1000003
                    _ = [acc] * 8
        except BaseException as e:  # noqa: BLE001 — containment oracle
            exceptions.append((f"workload-{n}", e))

    threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    for name, e in exceptions:
        print(f"[F5] exception leaked into {name}: {e!r}", flush=True)

    # do NOT p.stop()/normal-exit: scheduler worker is dead, join may wedge
    if any("acquired_time" in repr(e) for _, e in exceptions):
        print("[F5] REPRODUCED: lock-collector invariant violation raised inside an application thread", flush=True)
        os._exit(1)
    if exceptions:
        print(f"[F5] workload exceptions present but not the acquired_time invariant: {[repr(e) for _, e in exceptions]}", flush=True)
    print("[F5] NOT REPRODUCED standalone (see INVESTIGATION.md: rely on the pytest test)", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
