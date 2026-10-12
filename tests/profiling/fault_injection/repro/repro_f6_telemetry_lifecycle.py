"""F6: telemetry thread lifecycle around Profiler start/stop (observation).

Question: does the telemetry _TelemetryDependencyCollector thread started by
product activation outlive Profiler.stop(), and for how long? The suite saw it
linger after every stop in one run and vanish quickly in another (cadence
dependent). This is an OBSERVATION script - it always exits 0 and prints a
timeline for the investigator.
"""
import sys
import time

import _common


def telemetry_threads():
    from ddtrace.internal import _threads

    return [
        str(getattr(t, "name", t))
        for t in _threads.periodic_threads.values()
        if "telemetry" in str(getattr(t, "name", ""))
    ]


def main():
    p = _common.make_profiler()
    before = telemetry_threads()
    print(f"[F6] before start: {before}", flush=True)

    p.start()
    during = telemetry_threads()
    print(f"[F6] while running: {during}", flush=True)

    _common.stop_guarded(p, "F6")

    t0 = time.monotonic()
    seen_alive = False
    while time.monotonic() - t0 < 35:
        cur = telemetry_threads()
        if cur:
            seen_alive = True
            print(f"[F6] t+{time.monotonic() - t0:5.1f}s: telemetry threads still running: {cur}", flush=True)
        time.sleep(1.0)
    after = telemetry_threads()
    print(f"[F6] final state (t+35s): {after}", flush=True)
    print(
        "[F6] OBSERVATION: telemetry threads "
        + ("were still alive for some time after Profiler.stop()" if seen_alive else "did not outlive the stop window"),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
