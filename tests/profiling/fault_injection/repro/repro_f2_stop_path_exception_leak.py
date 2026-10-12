"""F2: exception during stop (final ddup.upload) strands Scheduler workers.

Finding: stop() contains the exception and returns, but scheduler worker
thread(s) remain registered/alive. Mirrors the suite flow: a faulted stop,
then a RESTART cycle (fresh profiler, fault still armed) — the suite's
stop-fault tests leak one thread per stop (own + restart via assert_restartable).
See INVESTIGATION.md F2. Exit 1 if reproduced.
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

    state = {"in_stop": False, "n": 0, "faulted": 0}
    orig = ddup.upload

    def upload_raises_during_stop(*a, **kw):
        state["n"] += 1
        if state["in_stop"]:
            state["faulted"] += 1
            raise RuntimeError("injected: upload during stop (F2)")
        return orig(*a, **kw)

    ddup.upload = upload_raises_during_stop
    try:
        # cycle 1: faulted stop
        p1 = _common.make_profiler()
        p1.start()
        churn(1.0)
        state["in_stop"] = True
        _common.stop_guarded(p1, "F2")
        state["in_stop"] = False
        time.sleep(0.5)
        leaked1 = _common.profiler_threads()
        print(f"[F2] cycle 1 (faulted stop) leftovers: {[str(getattr(t, 'name', t)) for t in leaked1]}", flush=True)

        # cycle 2: restart with the fault still armed (mirrors assert_restartable)
        p2 = _common.make_profiler()
        p2.start()
        churn(0.5)
        state["in_stop"] = True
        _common.stop_guarded(p2, "F2-restart")
        state["in_stop"] = False
        time.sleep(0.5)
        leaked2 = _common.profiler_threads()
        print(f"[F2] cycle 2 (restart stop) leftovers: {[str(getattr(t, 'name', t)) for t in leaked2]}", flush=True)

        print(f"[F2] upload calls: {state['n']} total, {state['faulted']} raised during stop windows", flush=True)
        if leaked2:
            print("[F2] REPRODUCED: stop-path exception strands scheduler thread(s)", flush=True)
            return 1
        if leaked1:
            print("[F2] PARTIAL: cycle 1 leaked but restart cycle clean", flush=True)
            return 1
        print("[F2] NOT REPRODUCED: no leftover profiler threads", flush=True)
        return 0
    finally:
        ddup.upload = orig


if __name__ == "__main__":
    sys.exit(main())
