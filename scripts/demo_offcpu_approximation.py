#!/usr/bin/env python3
"""
Demo: off-CPU time approximation in dd-trace-py stack v2 profiler.

Three threads run for ~2 seconds, each with a distinct blocking pattern:
  - sleeper-thread:       time.sleep loops          → cause: sleep
  - lock-waiter-thread:   blocked on a held lock    → cause: lock
  - spinner-thread:       busy loop                 → mostly on-CPU

Writes a pprof profile, then prints:
  1. Per-thread wall / cpu / off-cpu totals
  2. Off-cpu cause breakdown (sleep / lock / io / other)
  3. Top off-CPU call stacks per thread

Usage:
    python scripts/demo_offcpu_approximation.py [--duration SECS]

Requires: DD_PROFILING_STACK_V2_OFFCPU_TIME_ENABLED=true OR the script sets
it internally via ddup.config(offcpu_time_enabled=True).
"""

import argparse
from collections import defaultdict
import os
import queue
import socket
import sys
import tempfile
import threading
import time
from typing import Any


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack


WALL_TYPE = "wall-time"
CPU_TYPE = "cpu-time"
OFF_CPU_TYPE = "off-cpu-time"
OFF_CPU_CAUSE_LABEL = "off cpu cause"
THREAD_NAME_LABEL = "thread name"

CAUSES = ["sleep", "lock", "io", "other"]

_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"


def _h(text: str) -> str:
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


def _g(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def _y(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _ns_to_ms(ns: int) -> float:
    return ns / 1_000_000


def _rule(width: int = 80) -> str:
    return "─" * width


def _section(title: str, width: int = 80) -> None:
    print()
    print(_h(f"{'─── ' + title + ' ':─<{width}}"))


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------


def sleeper(duration: float) -> None:
    end = time.monotonic() + duration
    while time.monotonic() < end:
        time.sleep(0.05)


def lock_waiter(lock: threading.Lock, duration: float) -> None:
    end = time.monotonic() + duration
    while time.monotonic() < end:
        lock.acquire()
        lock.release()


def event_waiter(duration: float) -> None:
    event = threading.Event()
    end = time.monotonic() + duration
    while time.monotonic() < end:
        remaining = end - time.monotonic()
        event.wait(timeout=min(0.05, max(0.0, remaining)))


def queue_waiter(duration: float) -> None:
    q: queue.Queue[object] = queue.Queue()
    end = time.monotonic() + duration
    while time.monotonic() < end:
        remaining = end - time.monotonic()
        try:
            q.get(timeout=min(0.05, max(0.0, remaining)))
        except queue.Empty:
            pass


def io_waiter(duration: float) -> None:
    r, w = socket.socketpair()
    try:
        end = time.monotonic() + duration
        while time.monotonic() < end:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            r.settimeout(min(0.05, remaining))
            try:
                r.recv(1024)
            except (TimeoutError, OSError):
                pass
    finally:
        r.close()
        w.close()


def spinner(duration: float) -> None:
    end = time.monotonic() + duration
    x = 0
    while time.monotonic() < end:
        x = (x + 1) & 0xFFFF


def cpu_fibonacci(duration: float) -> None:
    """Pure-Python Fibonacci accumulation — fully on-CPU, visible Python frames."""
    end = time.monotonic() + duration
    a, b = 0, 1
    while time.monotonic() < end:
        a, b = b, (a + b) % (2**31)


def cpu_hash(duration: float) -> None:
    """SHA-256 in a tight loop — on-CPU via C extension, shows hashlib frame."""
    import hashlib  # noqa: PLC0415

    data = b"offcpu-demo-payload" * 64
    end = time.monotonic() + duration
    while time.monotonic() < end:
        hashlib.sha256(data).digest()


# ---------------------------------------------------------------------------
# pprof helpers
# ---------------------------------------------------------------------------


def _label(string_table: Any, sample: Any, key: str) -> Any:
    for lbl in sample.label:
        if string_table[lbl.key] == key:
            return lbl
    return None


def _stack_frames(profile: Any, sample: Any, max_frames: int = 5) -> list[tuple[str, str]]:
    """Return [(function_name, filename), ...] leaf-first, up to max_frames."""
    frames: list[tuple[str, str]] = []
    func_by_id = {f.id: f for f in profile.function}
    for loc_id in sample.location_id:
        loc = next((loc_ for loc_ in profile.location if loc_.id == loc_id), None)
        if loc is None:
            continue
        for line in loc.line:
            func = func_by_id.get(line.function_id)
            if func is None:
                continue
            name = profile.string_table[func.name]
            filename = os.path.basename(profile.string_table[func.filename])
            frames.append((name, filename))
            if len(frames) >= max_frames:
                return frames
    return frames


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=2.0, metavar="SECS")
    args = parser.parse_args()
    duration = args.duration

    if not ddup.is_available:
        raise SystemExit("ddup native extension not available — build first")

    pprof_prefix = os.path.join(tempfile.gettempdir(), "offcpu_demo")

    ddup.config(
        env="demo",
        service="offcpu-demo",
        version="rnd-week",
        output_filename=pprof_prefix,
        offcpu_time_enabled=True,
    )
    ddup.start()
    ddup.upload()

    held_lock = threading.Lock()
    held_lock.acquire()  # keep held so lock_waiter blocks

    t_sleep = threading.Thread(target=sleeper, args=(duration,), name="sleeper-thread")
    t_lock = threading.Thread(target=lock_waiter, args=(held_lock, duration), name="lock-waiter-thread")
    t_event = threading.Thread(target=event_waiter, args=(duration,), name="event-waiter-thread")
    t_queue = threading.Thread(target=queue_waiter, args=(duration,), name="queue-waiter-thread")
    t_io = threading.Thread(target=io_waiter, args=(duration,), name="io-waiter-thread")
    t_spin = threading.Thread(target=spinner, args=(duration,), name="spinner-thread")
    t_fib = threading.Thread(target=cpu_fibonacci, args=(duration,), name="cpu-fibonacci-thread")
    t_hash = threading.Thread(target=cpu_hash, args=(duration,), name="cpu-hash-thread")

    print(
        f"Profiling {duration:.1f}s  •  8 threads: "
        "sleeper / lock-waiter / event-waiter / queue-waiter / io-waiter / "
        "spinner / cpu-fibonacci / cpu-hash"
    )
    with stack.StackCollector():
        t_sleep.start()
        t_lock.start()
        t_event.start()
        t_queue.start()
        t_io.start()
        t_spin.start()
        t_fib.start()
        t_hash.start()
        time.sleep(duration)
        held_lock.release()
        t_sleep.join()
        t_lock.join()
        t_event.join()
        t_queue.join()
        t_io.join()
        t_spin.join()
        t_fib.join()
        t_hash.join()

    ddup.upload()

    # --- parse ---
    output_filename = pprof_prefix + "." + str(os.getpid())
    tests_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests")
    sys.path.insert(0, tests_dir)
    from profiling.collector import pprof_utils  # noqa: PLC0415

    profile = pprof_utils.parse_newest_profile(output_filename, assert_samples=False)
    st = profile.string_table

    def type_idx(name: str) -> int | None:
        try:
            return int(pprof_utils.get_sample_type_index(profile, name))
        except StopIteration:
            return None

    wall_idx = type_idx(WALL_TYPE)
    cpu_idx = type_idx(CPU_TYPE)
    off_cpu_idx = type_idx(OFF_CPU_TYPE)

    has_offcpu = off_cpu_idx is not None
    has_cause = has_offcpu and any(_label(st, s, OFF_CPU_CAUSE_LABEL) for s in profile.sample)

    # accumulate per-thread totals and cause breakdown
    totals: dict[str, dict[str, int]] = {}
    cause_totals: dict[str, dict[str, int]] = {}
    # heaviest off-CPU sample per thread (for stack display)
    heaviest: dict[str, tuple[int, object]] = {}  # thread_name → (off_cpu_ns, sample)

    for sample in profile.sample:
        lbl = _label(st, sample, THREAD_NAME_LABEL)
        if lbl is None:
            continue
        tname = st[lbl.str]

        if tname not in totals:
            totals[tname] = {"wall": 0, "cpu": 0, "off_cpu": 0}
            cause_totals[tname] = defaultdict(int)

        if wall_idx is not None:
            totals[tname]["wall"] += sample.value[wall_idx]
        if cpu_idx is not None:
            totals[tname]["cpu"] += sample.value[cpu_idx]

        off_ns = sample.value[off_cpu_idx] if has_offcpu else 0
        totals[tname]["off_cpu"] += off_ns

        if has_cause and off_ns > 0:
            cause_lbl = _label(st, sample, OFF_CPU_CAUSE_LABEL)
            cause = st[cause_lbl.str] if cause_lbl else "other"
            cause_totals[tname][cause] += off_ns
            cur_best, _ = heaviest.get(tname, (0, None))
            if off_ns > cur_best:
                heaviest[tname] = (off_ns, sample)

    # ── 1. Time summary ─────────────────────────────────────────────────────
    _section("Time summary")
    col = 22
    print(f"{'Thread':<{col}}  {'wall (ms)':>12}  {'cpu (ms)':>12}  {'off-cpu (ms)':>14}  {'off-cpu %':>10}")
    print(_rule())
    for tname in sorted(totals):
        d = totals[tname]
        wall_ms = _ns_to_ms(d["wall"])
        cpu_ms = _ns_to_ms(d["cpu"])
        off_ms = _ns_to_ms(d["off_cpu"])
        pct = (off_ms / wall_ms * 100) if wall_ms > 0 else 0.0
        pct_str = _g(f"{pct:>9.1f}%") if pct > 80 else f"{pct:>9.1f}%"
        print(f"{tname:<{col}}  {wall_ms:>12.1f}  {cpu_ms:>12.1f}  {off_ms:>14.1f}  {pct_str}")

    if not has_offcpu:
        print()
        print(_y("  Off-CPU collection disabled — rebuild with offcpu_time_enabled=True"))
        return

    # ── 2. Cause breakdown ──────────────────────────────────────────────────
    if has_cause:
        _section("Off-CPU cause breakdown")
        header = f"{'Thread':<{col}}" + "".join(f"  {c:>14}" for c in CAUSES)
        print(header)
        print(_rule())
        for tname in sorted(cause_totals):
            row = f"{tname:<{col}}"
            for cause in CAUSES:
                ms = _ns_to_ms(cause_totals[tname].get(cause, 0))
                cell = f"{ms:>13.1f}ms"
                row += "  " + (_g(cell) if ms > 100 else _DIM + cell + _RESET)
            print(row)
    else:
        print()
        print(_y("  'off cpu cause' label not present — rebuild from vlad/profiling-offcpu-cause-label"))

    # ── 3. Top off-CPU stacks ───────────────────────────────────────────────
    _section("Top off-CPU stacks  (heaviest single sample per thread)")
    for tname in sorted(heaviest):
        off_ns, sample = heaviest[tname]
        cause_lbl = _label(st, sample, OFF_CPU_CAUSE_LABEL)
        cause = st[cause_lbl.str] if cause_lbl else "?"
        frames = _stack_frames(profile, sample, max_frames=6)
        print()
        print(f"  {_bold_thread(tname)}  cause={_g(cause)}  {_ns_to_ms(off_ns):.1f} ms")
        if frames:
            for i, (fname, ffile) in enumerate(frames):
                prefix = "  └─ " if i == len(frames) - 1 else "  ├─ "
                print(f"    {prefix}{fname:<40} {_DIM}{ffile}{_RESET}")
        else:
            print("    (no frames captured)")
    print()


def _bold_thread(name: str) -> str:
    return f"{_BOLD}[{name}]{_RESET}"


if __name__ == "__main__":
    main()
