"""Long-running demo for gcmonitor.

Starts the GC leak monitor, then deliberately leaks a few different kinds of
objects over time so you can watch the snapshot JSON files grow. Leaked objects
accumulate in module-level containers and are never released, so their
``age_snapshots`` / ``age_seconds`` keep climbing across snapshots.

Run::

    python gcmonitor_demo.py
    # snapshots are written to ./gc_snapshots/snapshot_*.json
    # Ctrl+C to stop.

Then inspect the latest snapshot, e.g.::

    python -c "import json,glob; print(open(sorted(glob.glob('gc_snapshots/*.json'))[-1]).read())"
"""

from __future__ import annotations

import signal
import sys
import time
from types import FrameType
from typing import Any

import gcmonitor


# Module-level sinks: anything we put here lives forever -> shows up as a leak.
_leaked_records: list[dict[str, Any]] = []
_leaked_buffers: list[bytearray] = []
_leaked_cache: dict[int, "LeakyEntry"] = {}


class LeakyEntry:
    """A small user-defined object so the demo surfaces a non-builtin type."""

    def __init__(self, seq: int) -> None:
        self.seq = seq
        self.payload = "x" * 32
        self.created_at = time.time()

    def __repr__(self) -> str:
        return f"<LeakyEntry seq={self.seq}>"


def _leak_once(seq: int) -> None:
    _leaked_records.append({"seq": seq, "ts": time.time(), "note": "leaked record"})
    _leaked_buffers.append(bytearray(1024))
    _leaked_cache[seq] = LeakyEntry(seq)


def main() -> None:
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    leak_period = interval / 4 if interval > 0 else 2.0

    monitor = gcmonitor.start(interval=interval, output_dir="gc_snapshots")
    print(
        f"gcmonitor started (interval={interval}s). "
        f"Leaking ~every {leak_period:.1f}s. Writing to ./gc_snapshots/. "
        f"Press Ctrl+C to stop."
    )

    stopping = {"flag": False}

    def _handle_sigint(signum: int, frame: FrameType | None) -> None:
        stopping["flag"] = True

    signal.signal(signal.SIGINT, _handle_sigint)

    seq = 0
    try:
        while not stopping["flag"]:
            _leak_once(seq)
            seq += 1
            if seq % 10 == 0:
                print(
                    f"leaked {seq} batches "
                    f"(records={len(_leaked_records)}, "
                    f"buffers={len(_leaked_buffers)}, "
                    f"cache={len(_leaked_cache)})"
                )
            time.sleep(leak_period)
    finally:
        print("stopping monitor (flushing final snapshot)...")
        monitor.stop()
        print("done.")


if __name__ == "__main__":
    main()
