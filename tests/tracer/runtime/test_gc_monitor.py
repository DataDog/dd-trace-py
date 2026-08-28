import gc
from types import FrameType
from typing import Optional

from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor
from ddtrace.internal.runtime.gc_monitor import GCPauseSnapshot


def test_callback_installed_only_while_acquired() -> None:
    monitor: GCPauseMonitor = GCPauseMonitor()
    assert monitor._on_gc not in gc.callbacks
    monitor.acquire()
    try:
        assert monitor._on_gc in gc.callbacks
        monitor.acquire()
        assert gc.callbacks.count(monitor._on_gc) == 1
        monitor.release()
        assert monitor._on_gc in gc.callbacks
    finally:
        monitor.release()
    assert monitor._on_gc not in gc.callbacks


def test_snapshot_records_real_collection() -> None:
    monitor: GCPauseMonitor = GCPauseMonitor()
    monitor.acquire()
    try:
        monitor.snapshot_and_reset()
        gc.collect()
        snap: GCPauseSnapshot = monitor.snapshot_and_reset()
    finally:
        monitor.release()

    assert snap.n_pauses >= 1
    assert snap.total_ns > 0
    assert snap.max_ns > 0
    assert snap.max_ns <= snap.total_ns
    assert sum(g[0] for g in snap.per_gen) == snap.n_pauses
    assert sum(g[1] for g in snap.per_gen) == snap.total_ns


def test_listener_sees_generation_and_frame() -> None:
    events: list[tuple[int, int, int, Optional[FrameType]]] = []

    def listener(gen: int, pause_ns: int, start_ns: int, frame: Optional[FrameType]) -> None:
        events.append((gen, pause_ns, start_ns, frame))

    monitor: GCPauseMonitor = GCPauseMonitor()
    monitor.add_listener(listener)
    monitor.acquire()
    try:
        gc.collect()
    finally:
        monitor.remove_listener(listener)
        monitor.release()

    assert events
    gen: int
    pause_ns: int
    start_ns: int
    frame: Optional[FrameType]
    gen, pause_ns, start_ns, frame = events[-1]
    assert gen in (0, 1, 2)
    assert pause_ns > 0
    assert start_ns > 0
    assert frame is not None


def test_release_clears_in_flight_start() -> None:
    monitor: GCPauseMonitor = GCPauseMonitor()
    monitor.acquire()
    monitor._on_gc("start", {"generation": 0})
    assert monitor._start_ns[0] != 0
    monitor.release()
    assert monitor._start_ns == [0, 0, 0]

    monitor.acquire()
    try:
        monitor._on_gc("stop", {"generation": 0})
        snap: GCPauseSnapshot = monitor.snapshot_and_reset()
    finally:
        monitor.release()

    assert snap.n_pauses == 0
    assert snap.total_ns == 0


def test_reset_drops_window() -> None:
    monitor: GCPauseMonitor = GCPauseMonitor()
    monitor.acquire()
    try:
        gc.collect()
        monitor.reset()
        snap: GCPauseSnapshot = monitor.snapshot_and_reset()
    finally:
        monitor.release()

    assert snap.n_pauses == 0
    assert snap.total_ns == 0
    assert snap.max_ns == 0
