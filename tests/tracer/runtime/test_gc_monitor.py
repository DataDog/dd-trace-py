import gc

from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor


def test_callback_installed_only_while_acquired():
    monitor = GCPauseMonitor()
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


def test_snapshot_records_real_collection():
    monitor = GCPauseMonitor()
    monitor.acquire()
    try:
        monitor.snapshot_and_reset()
        gc.collect()
        snap = monitor.snapshot_and_reset()
    finally:
        monitor.release()

    assert snap.n_pauses >= 1
    assert snap.total_ns > 0
    assert snap.max_ns > 0
    assert snap.max_ns <= snap.total_ns
    assert sum(g[0] for g in snap.per_gen) == snap.n_pauses
    assert sum(g[1] for g in snap.per_gen) == snap.total_ns


def test_listener_sees_generation_and_frame():
    events = []

    def listener(gen, pause_ns, start_ns, frame):
        events.append((gen, pause_ns, start_ns, frame))

    monitor = GCPauseMonitor()
    monitor.add_listener(listener)
    monitor.acquire()
    try:
        gc.collect()
    finally:
        monitor.remove_listener(listener)
        monitor.release()

    assert events
    gen, pause_ns, start_ns, frame = events[-1]
    assert gen in (0, 1, 2)
    assert pause_ns > 0
    assert start_ns > 0
    assert frame is not None


def test_reset_drops_window():
    monitor = GCPauseMonitor()
    monitor.acquire()
    try:
        gc.collect()
        monitor.reset()
        snap = monitor.snapshot_and_reset()
    finally:
        monitor.release()

    assert snap.n_pauses == 0
    assert snap.total_ns == 0
    assert snap.max_ns == 0
