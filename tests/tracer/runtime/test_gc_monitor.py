from collections.abc import Iterator
import contextlib
import gc
import threading

from ddtrace.internal import forksafe
from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor
from ddtrace.internal.runtime.gc_monitor import GCPauseSnapshot


# Tests that call _on_gc manually while the monitor's gc.callbacks hook is
# installed must run with automatic GC disabled.
@contextlib.contextmanager
def _automatic_gc_disabled() -> Iterator[None]:
    was_enabled: bool = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def test_callback_installed_only_while_acquired() -> None:
    monitor: GCPauseMonitor = GCPauseMonitor()
    assert not any(cb is monitor._gc_hook for cb in gc.callbacks)
    monitor.acquire()
    try:
        assert any(cb is monitor._gc_hook for cb in gc.callbacks)
        monitor.acquire()
        assert sum(1 for cb in gc.callbacks if cb is monitor._gc_hook) == 1
        monitor.release()
        assert any(cb is monitor._gc_hook for cb in gc.callbacks)
    finally:
        monitor.release()
    assert not any(cb is monitor._gc_hook for cb in gc.callbacks)


def test_last_release_unregisters_fork_hook() -> None:
    monitor: GCPauseMonitor = GCPauseMonitor()
    assert monitor._fork_hook not in forksafe._registry
    monitor.acquire()
    try:
        assert monitor._fork_hook in forksafe._registry
        monitor.acquire()
        assert forksafe._registry.count(monitor._fork_hook) == 1
        monitor.release()
        assert monitor._fork_hook in forksafe._registry
    finally:
        monitor.release()
    assert monitor._fork_hook not in forksafe._registry

    monitor.acquire()
    try:
        assert monitor._fork_hook in forksafe._registry
        assert forksafe._registry.count(monitor._fork_hook) == 1
    finally:
        monitor.release()
    assert monitor._fork_hook not in forksafe._registry


def test_release_uninstalls_by_identity_not_equality() -> None:
    """`in`/`remove` use equality; a matching foreign callback must not block uninstall."""
    monitor: GCPauseMonitor = GCPauseMonitor()

    class _Confuser:
        def __eq__(self, other: object) -> bool:
            return True

    confuser: _Confuser = _Confuser()
    monitor.acquire()
    gc.callbacks.append(confuser)  # type: ignore[arg-type]
    try:
        monitor.release()
        assert not any(cb is monitor._gc_hook for cb in gc.callbacks)
        assert confuser in gc.callbacks
    finally:
        gc.callbacks[:] = [cb for cb in gc.callbacks if cb is not confuser]


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
    with _automatic_gc_disabled():
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


def test_start_is_ignored_while_unheld() -> None:
    """A start callback that runs after release() must not record a time.

    gc.callbacks.remove() does not retract a callback that is already running, so
    without the refcount check the timestamp survives to pair with a stop after the
    next acquire and reports the whole gap as one pause.
    """
    with _automatic_gc_disabled():
        monitor: GCPauseMonitor = GCPauseMonitor()
        monitor.acquire()
        monitor.release()

        monitor._on_gc("start", {"generation": 0})
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
    """release() and reset() must zero _start_ns in place."""
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
    """A fork can happen while another thread holds the lock, so the child must not inherit it held."""
    monitor: GCPauseMonitor = GCPauseMonitor()
    assert isinstance(monitor._lock, forksafe.ResetObject)


def test_install_uses_the_prebound_hook() -> None:
    """acquire()/release() must install and remove the cached bound method.

    Evaluating self._on_gc builds a fresh bound method, and release() finds the
    installed hook by identity, so a fresh bound method would never match it.
    """
    monitor: GCPauseMonitor = GCPauseMonitor()
    monitor.acquire()
    try:
        assert [cb for cb in gc.callbacks if cb is monitor._gc_hook] == [monitor._gc_hook]
        assert monitor._fork_hook in forksafe._registry
    finally:
        monitor.release()

    assert all(cb is not monitor._gc_hook for cb in gc.callbacks)


def test_callback_does_not_wait_for_the_lock() -> None:
    """CPython can run a collection on a thread that holds the lock.

    If _on_gc waits for the lock, that collection never finishes, and CPython starts
    no other collection in the process.
    """
    monitor: GCPauseMonitor = GCPauseMonitor()
    # Count as acquired without installing the hook. An installed hook would make
    # every later collection in this process reach the stuck callback.
    monitor._refcount = 1

    def collect_while_locked() -> None:
        with monitor._lock:
            monitor._on_gc("start", {"generation": 0})
            monitor._on_gc("stop", {"generation": 0})

    thread: threading.Thread = threading.Thread(target=collect_while_locked, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert monitor.snapshot_and_reset().n_pauses == 1
