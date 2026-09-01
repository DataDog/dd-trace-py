import gc

from ddtrace.internal import forksafe
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


def test_last_release_unregisters_fork_hook() -> None:
    monitor: GCPauseMonitor = GCPauseMonitor()
    assert monitor.reset not in forksafe._registry
    monitor.acquire()
    try:
        assert monitor.reset in forksafe._registry
        monitor.acquire()
        assert forksafe._registry.count(monitor.reset) == 1
        monitor.release()
        assert monitor.reset in forksafe._registry
    finally:
        monitor.release()
    assert monitor.reset not in forksafe._registry

    monitor.acquire()
    try:
        assert monitor.reset in forksafe._registry
        assert forksafe._registry.count(monitor.reset) == 1
    finally:
        monitor.release()
    assert monitor.reset not in forksafe._registry


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


def test_clears_reuse_the_start_list() -> None:
    """release() and reset() must zero _start_ns in place.

    Rebinding it allocates while the lock is held, and a collection triggered by
    that allocation reenters _on_gc on the same thread, which deadlocks on a
    non-reentrant lock.
    """
    monitor: GCPauseMonitor = GCPauseMonitor()
    starts: list[int] = monitor._start_ns

    monitor.acquire()
    monitor._on_gc("start", {"generation": 1})
    monitor.release()
    assert monitor._start_ns is starts
    assert starts == [0, 0, 0]

    monitor.acquire()
    monitor._on_gc("start", {"generation": 2})
    monitor.reset()
    monitor.release()
    assert monitor._start_ns is starts
    assert starts == [0, 0, 0]


def test_lock_is_forksafe() -> None:
    """_on_gc runs on whatever thread collects, so the child must not inherit it held."""
    monitor: GCPauseMonitor = GCPauseMonitor()
    assert isinstance(monitor._lock, forksafe.ResetObject)


def test_install_uses_the_prebound_hook() -> None:
    """acquire()/release() must install and remove the cached bound method.

    Evaluating self._on_gc builds a fresh bound method, which is a GC-tracked
    allocation. Doing that while the callback is installed and the lock is held
    could start a collection that reenters _on_gc on the same thread.
    """
    monitor: GCPauseMonitor = GCPauseMonitor()
    monitor.acquire()
    try:
        assert [cb for cb in gc.callbacks if cb is monitor._gc_hook] == [monitor._gc_hook]
        assert monitor._fork_hook in forksafe._registry
    finally:
        monitor.release()

    assert all(cb is not monitor._gc_hook for cb in gc.callbacks)
