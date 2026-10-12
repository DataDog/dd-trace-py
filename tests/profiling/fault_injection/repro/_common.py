"""Shared helpers for standalone repros (no pytest, no faultlib dependency).

Exit-code convention across repros:
  0  finding NOT reproduced
  1  finding REPRODUCED
  3  safety guard tripped (e.g. stop() itself wedged - see F3)
"""
import os
import sys
import threading

REPO = os.environ.get("FI_REPO", os.path.expanduser("~/dd/dd-trace-py-fi"))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def make_profiler(interval=0.2):
    from ddtrace.profiling import profiler as profiler_mod

    p = profiler_mod.Profiler(
        _memory_collector_enabled=True,
        _stack_collector_enabled=True,
        _lock_collector_enabled=True,
        _exception_profiling_enabled=True,
        _pytorch_collector_enabled=False,
    )
    p._scheduler.interval = interval
    return p


def profiler_threads():
    """Entries left in the native periodic-threads registry owned by ddtrace.profiling."""
    from ddtrace.internal import _threads

    return [
        t
        for t in _threads.periodic_threads.values()
        if "ddtrace.profiling" in str(getattr(t, "name", ""))
    ]


def stop_guarded(p, label, timeout=20.0):
    """Call p.stop() from a watchdog thread; os._exit(3) if it wedges (F3 family)."""
    done = threading.Event()

    def _run():
        try:
            p.stop()
        except BaseException as e:  # noqa: BLE001
            print(f"[{label}] stop() raised: {e!r}", flush=True)
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    if not done.wait(timeout):
        print(f"[{label}] stop() did not return within {timeout}s (F3-style wedge)", flush=True)
        os._exit(3)
