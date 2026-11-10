from collections.abc import Iterator
import contextlib
import gc
import queue
import sys
import threading
import time
from typing import Any
from typing import Callable

import pytest

from ddtrace.internal import forksafe
from ddtrace.internal.runtime import gc_monitor
from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor
from ddtrace.internal.runtime.gc_monitor import GCPauseSnapshot
from ddtrace.internal.runtime.gc_monitor import PauseListener


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
    """release() and reset() must zero _start_ns in place.

    Rebinding it allocates while the lock is held, and _on_gc skips the pause of a
    collection that the allocation triggers.
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


def test_stop_while_lock_held_on_same_thread_does_not_deadlock() -> None:
    """Signal-style stop must not wait on the monitor lock this thread already holds.

    Profiler SIGTERM/SIGINT handlers run on the main thread between bytecodes. If
    that lands while _on_gc (or snapshot_and_reset) holds the non-reentrant lock,
    blocking remove_listener/release deadlocks. Hold the lock the way _on_gc does
    after a successful acquire, then stop on the same thread.
    """
    result: queue.Queue[str] = queue.Queue()

    def target() -> None:
        monitor: GCPauseMonitor = GCPauseMonitor()
        monitor.acquire()

        def listener(gen: int, pause_ns: int) -> None:
            return None

        pause_listener: PauseListener = listener
        monitor.add_listener(pause_listener)

        # Prefer the ownership-tracked helper when present so deferred stop matches
        # production. Older trees fall back to the raw lock and hang on release —
        # that is the bug this test pins.
        lock_acquire: Callable[[bool], bool] = getattr(monitor, "_lock_acquire", monitor._lock.acquire)
        if not lock_acquire(False):
            result.put("acquire-failed")
            return
        try:
            monitor.remove_listener(pause_listener)
            monitor.release()
            if getattr(monitor, "_lock_owned", lambda: False)():
                work: list[Any] = monitor._drain_deferred_locked()
                monitor._unlock_and_run(work)
            else:
                monitor._lock.release()
            if monitor._refcount != 0:
                result.put(f"refcount={monitor._refcount}")
                return
            if any(cb is monitor._gc_hook for cb in gc.callbacks):
                result.put("callback-still-installed")
                return
            if pause_listener in monitor._listeners:
                result.put("listener-still-registered")
                return
            result.put("ok")
        except Exception as exc:
            result.put(repr(exc))

    thread: threading.Thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "remove_listener/release deadlocked on the monitor lock"
    assert result.get_nowait() == "ok"


def test_snapshot_never_splits_a_pause(monkeypatch) -> None:
    """A pause must land wholly in one snapshot, or in none.

    Before Python 3.10, and on free-threaded builds, another thread can run _on_gc
    between any two statements of snapshot_and_reset. A pause that lands between the
    reads and the reset must not vanish or pair a new maximum with an old total.
    """
    clock: list[int] = [0]
    monkeypatch.setattr(gc_monitor, "_pause_clock_ns", lambda: clock[0])
    snapshot_code = GCPauseMonitor.snapshot_and_reset.__code__
    previous_trace = sys.gettrace()
    target: int = 0
    while True:
        monitor: GCPauseMonitor = GCPauseMonitor()
        # Count as acquired without installing the hook, so that only the injected
        # pause reaches _on_gc.
        monitor._refcount = 1
        monitor._count, monitor._total_ns, monitor._max_ns = 1, 5, 5
        seen: list[int] = [0]
        injected: list[bool] = [False]

        def trace(frame, event, arg):
            if frame.f_code is not snapshot_code:
                return None
            if event == "line":
                if seen[0] == target:
                    clock[0] = 100
                    monitor._on_gc("start", {"generation": 0})
                    clock[0] = 1100
                    monitor._on_gc("stop", {"generation": 0})
                    injected[0] = True
                seen[0] += 1
            return trace

        sys.settrace(trace)
        try:
            first: GCPauseSnapshot = monitor.snapshot_and_reset()
        finally:
            sys.settrace(previous_trace)
        if not injected[0]:
            break
        second: GCPauseSnapshot = monitor.snapshot_and_reset()

        assert (tuple(first), tuple(second)) in {
            ((2, 1005, 1000), (0, 0, 0)),
            ((1, 5, 5), (1, 1000, 1000)),
            ((1, 5, 5), (0, 0, 0)),
        }, f"pause injected before line {target}: {first}, {second}"
        target += 1
    assert target > 0


def test_skipped_stop_does_not_pair_with_a_later_stop() -> None:
    """A stop that skips its sample must not leave its start for a later stop.

    The later stop would otherwise report the whole gap since that start as one pause.
    """
    monitor: GCPauseMonitor = GCPauseMonitor()
    monitor._refcount = 1

    monitor._on_gc("start", {"generation": 0})
    with monitor._lock:
        monitor._on_gc("stop", {"generation": 0})
        monitor._on_gc("start", {"generation": 0})
    monitor._on_gc("stop", {"generation": 0})

    assert monitor.snapshot_and_reset().n_pauses == 0


def test_pause_clock_matches_platform() -> None:
    """Wall time on Windows or when thread_time_ns is missing; else thread CPU.

    The GIL-wait exclusion test below only covers the thread-time path. This
    pins the platform choice so the wall-time branch is not untested.
    """
    expected: object = (
        time.monotonic_ns if sys.platform == "win32" or not hasattr(time, "thread_time_ns") else time.thread_time_ns
    )
    assert gc_monitor._pause_clock_ns is expected


@pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(time, "thread_time_ns"),
    reason="pause clock is wall time when thread_time_ns is missing or too coarse",
)
def test_pause_excludes_time_other_threads_run() -> None:
    """Time that another thread runs during a collection is not a stop-the-world pause.

    Python code inside a collection (gc.callbacks, finalizers) can hand the GIL to
    another thread, which keeps it for as long as it runs. Wall time between start
    and stop would report all of that as one pause.

    Drive start/stop directly so a large heap's real GC work is not inside the
    measured window (that noise flaked under CI load against a 200ms budget).
    """
    other_ran_ns: int = 500_000_000
    go: threading.Event = threading.Event()
    other_done: threading.Event = threading.Event()
    ready: threading.Event = threading.Event()

    def other_thread() -> None:
        ready.set()
        go.wait()
        deadline: int = time.monotonic_ns() + other_ran_ns
        while time.monotonic_ns() < deadline:
            pass
        other_done.set()

    thread: threading.Thread = threading.Thread(target=other_thread, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)

    monitor: GCPauseMonitor = GCPauseMonitor()
    # Count as acquired without installing the hook; only the injected pause is timed.
    monitor._refcount = 1
    try:
        monitor.snapshot_and_reset()
        monitor._on_gc("start", {"generation": 0})
        wall_start: int = time.monotonic_ns()
        go.set()
        assert other_done.wait(timeout=5)
        wall_handoff_ns: int = time.monotonic_ns() - wall_start
        monitor._on_gc("stop", {"generation": 0})
        snap: GCPauseSnapshot = monitor.snapshot_and_reset()
    finally:
        go.set()
        thread.join(timeout=5)
        monitor._refcount = 0

    assert wall_handoff_ns >= other_ran_ns // 2, wall_handoff_ns
    assert snap.n_pauses == 1
    assert snap.max_ns < wall_handoff_ns // 5, (snap, wall_handoff_ns)
