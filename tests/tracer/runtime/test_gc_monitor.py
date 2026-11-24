from collections.abc import Iterator
import contextlib
import gc
import inspect
import sys
import threading
import time
from types import CodeType
from types import FrameType
from typing import Any
from typing import Callable
from typing import Optional

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


def test_listeners_get_each_pause_after_the_lock_is_released() -> None:
    monitor: GCPauseMonitor = GCPauseMonitor()
    calls: list[tuple[int, int, bool]] = []

    def failing_listener(generation: int, pause_ns: int) -> None:
        raise RuntimeError("a listener error must not escape the GC callback")

    def recording_listener(generation: int, pause_ns: int) -> None:
        lock_free: bool = monitor._lock.acquire(False)
        if lock_free:
            monitor._lock.release()
        calls.append((generation, pause_ns, lock_free))

    with _automatic_gc_disabled():
        monitor.acquire()
        try:
            monitor.add_listener(failing_listener)
            monitor.add_listener(recording_listener)
            monitor._on_gc("start", {"generation": 1})
            monitor._on_gc("stop", {"generation": 1})
            monitor.remove_listener(recording_listener)
            monitor._on_gc("start", {"generation": 1})
            monitor._on_gc("stop", {"generation": 1})
            snapshot: GCPauseSnapshot = monitor.snapshot_and_reset()
        finally:
            monitor.release()

    assert len(calls) == 1
    generation, pause_ns, lock_free = calls[0]
    assert generation == 1
    assert pause_ns >= 0
    assert lock_free
    assert snapshot.n_pauses == 2


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


def test_fork_resets_the_plain_lock_in_place_before_other_child_hooks() -> None:
    """A fork can happen while another thread holds the lock, so the child must not inherit it held.

    The lock is plain, because _on_gc takes it on every collection. The child resets it from a wrapper hook,
    which runs before the regular after-fork hooks, reset() among them, that take it.
    """
    monitor: GCPauseMonitor = GCPauseMonitor()
    lock: Any = monitor._lock
    assert not isinstance(lock, forksafe.ResetObject)
    assert gc_monitor._reset_monitor_locks in forksafe._registry_child_wrappers
    assert lock.acquire(False)
    reset: Iterator[None] = gc_monitor._reset_monitor_locks()
    next(reset)
    assert monitor._lock is lock
    assert not lock.locked()
    with pytest.raises(StopIteration):
        next(reset)


@pytest.mark.subprocess(err=None)
def test_real_fork_resets_a_lock_held_at_fork_time() -> None:
    # Another thread holds the monitor lock while the process forks, from the main thread and then from a second
    # thread. Each child runs reset() as an after-fork hook; it must find the lock reset instead of waiting forever
    # for a thread it does not have, and keep timing collections.
    import gc
    import os
    import signal
    import threading
    import time
    import traceback

    from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor

    # Only explicit collections, so each child's snapshot holds exactly its own.
    gc.disable()
    monitor: GCPauseMonitor = GCPauseMonitor()
    monitor.acquire()
    lock_held: threading.Event = threading.Event()
    release_lock: threading.Event = threading.Event()

    def hold_lock() -> None:
        with monitor._lock:
            lock_held.set()
            release_lock.wait()

    def fork_and_wait(statuses: list[int]) -> None:
        pid: int = os.fork()
        if pid == 0:
            exit_code: int = 1
            try:
                assert not monitor._lock.locked()
                gc.collect(0)
                assert monitor.snapshot_and_reset().n_pauses == 1
                exit_code = 0
            except BaseException:
                os.write(2, traceback.format_exc().encode())
            finally:
                os._exit(exit_code)
        for _ in range(600):
            waited: tuple[int, int] = os.waitpid(pid, os.WNOHANG)
            if waited[0] == pid:
                statuses.append(waited[1])
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        statuses.append(-1)

    holder: threading.Thread = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_held.wait(10)
    statuses: list[int] = []
    try:
        fork_and_wait(statuses)
        forker: threading.Thread = threading.Thread(target=fork_and_wait, args=(statuses,))
        forker.start()
        forker.join()
    finally:
        release_lock.set()
        holder.join()
        monitor.release()

    assert len(statuses) == 2, statuses
    for status in statuses:
        assert status != -1, "a forked child hung, so an after-fork hook waited on the lock held at fork time"
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status


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


class _LockWithStop:
    """Monitor lock that runs a stop the way a SIGTERM/SIGINT handler would.

    Python signal handlers run on the main thread between bytecodes, so a profiler
    stop can land on the thread that holds the monitor lock: right after acquire()
    returns, or right before release(). after_release runs once the lock is free
    again, before its caller goes on, so another thread can take the lock then.
    """

    def __init__(self, lock: Any) -> None:
        self._lock: Any = lock
        self.after_acquire: Optional[Callable[[], None]] = None
        self.before_release: Optional[Callable[[], None]] = None
        self.after_release: Optional[Callable[[], None]] = None

    def acquire(self, blocking: bool = True) -> bool:
        acquired: bool = self._lock.acquire(blocking)
        stop: Optional[Callable[[], None]] = self.after_acquire
        if acquired and stop is not None:
            self.after_acquire = None
            stop()
        return acquired

    def release(self) -> None:
        stop: Optional[Callable[[], None]] = self.before_release
        if stop is not None:
            self.before_release = None
            stop()
        self._lock.release()
        after: Optional[Callable[[], None]] = self.after_release
        if after is not None:
            self.after_release = None
            after()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def _finishes(section: Callable[[], object]) -> bool:
    # Another thread, so a section that deadlocks fails the test instead of hanging it.
    thread: threading.Thread = threading.Thread(target=section, daemon=True)
    thread.start()
    thread.join(timeout=5)
    return not thread.is_alive()


@contextlib.contextmanager
def _acquired_monitor_with_listener() -> Iterator[tuple[GCPauseMonitor, PauseListener]]:
    monitor: GCPauseMonitor = GCPauseMonitor()

    def listener(gen: int, pause_ns: int) -> None:
        return None

    pause_listener: PauseListener = listener
    with _automatic_gc_disabled():
        monitor.acquire()
        monitor.add_listener(pause_listener)
        try:
            yield monitor, pause_listener
        finally:
            # A failed test can leave the stop queued; do not leak the hook into later tests.
            gc_monitor._remove_gc_callback(monitor._gc_hook)
            if monitor._fork_hook in forksafe._registry:
                forksafe.unregister(monitor._fork_hook)


def _assert_stopped(monitor: GCPauseMonitor, pause_listener: PauseListener) -> None:
    assert monitor._refcount == 0
    assert all(cb is not monitor._gc_hook for cb in gc.callbacks)
    assert monitor._fork_hook not in forksafe._registry
    assert pause_listener not in monitor._listeners


@pytest.mark.parametrize("boundary", ["after_acquire", "before_release"])
@pytest.mark.parametrize("section", ["acquire", "_on_gc", "reset", "snapshot_and_reset"])
def test_signal_stop_on_the_thread_that_holds_the_lock(section: str, boundary: str) -> None:
    """Stop must not wait for a lock its own thread holds, and must be done when the section returns."""
    monitor: GCPauseMonitor
    pause_listener: PauseListener
    with _acquired_monitor_with_listener() as (monitor, pause_listener):

        def stop() -> None:
            monitor.remove_listener(pause_listener)
            monitor.release()

        def run_section() -> None:
            if section == "_on_gc":
                monitor._on_gc("stop", {"generation": 0})
            else:
                getattr(monitor, section)()

        if section == "acquire":
            # Start from released, so that the stop cancels the acquire under test.
            monitor.release()
        monitor._on_gc("start", {"generation": 0})
        lock: _LockWithStop = _LockWithStop(monitor._lock)
        setattr(lock, boundary, stop)
        monitor._lock = lock  # type: ignore[assignment]

        assert _finishes(run_section), "stop waited for the lock its own thread holds"
        _assert_stopped(monitor, pause_listener)


def test_interrupt_while_a_section_takes_the_lock_does_not_leave_it_held() -> None:
    """A signal handler that raises while a section takes the lock must not leave it held.

    The default SIGINT handler raises KeyboardInterrupt between bytecodes, which can be
    right after a bare acquire() returns and before its try starts. A lock left held
    blocks every later snapshot, and stop work queued after that is never applied.
    """
    entering_code: CodeType = inspect.unwrap(GCPauseMonitor._holding_lock).__code__
    previous_trace: Optional[Callable[..., Any]] = sys.gettrace()
    target: int = 0
    while True:
        monitor: GCPauseMonitor = GCPauseMonitor()
        seen: list[int] = [0]
        state: list[str] = ["entering"]

        def trace(frame: FrameType, event: str, arg: object) -> Optional[Callable[..., Any]]:
            if frame.f_code is not entering_code or state[0] != "entering":
                return None
            if event == "return":
                # Reached the yield: the section now holds the lock inside its protection.
                state[0] = "entered"
            elif event == "line":
                if seen[0] == target:
                    state[0] = "interrupted"
                    raise KeyboardInterrupt
                seen[0] += 1
            return trace

        sys.settrace(trace)
        try:
            monitor.reset()
        except KeyboardInterrupt:
            pass
        finally:
            sys.settrace(previous_trace)
        assert not monitor._lock.locked(), f"an interrupt at line event {target} of taking the lock left it held"
        if state[0] != "interrupted":
            break
        target += 1
    assert target > 0


def test_signal_stop_inside_reset_is_done_when_reset_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop work queued while reset() holds the lock must not wait for some later collection."""
    monitor: GCPauseMonitor
    pause_listener: PauseListener
    with _acquired_monitor_with_listener() as (monitor, pause_listener):
        clear_starts: Callable[[], None] = monitor._clear_starts
        stopped: list[bool] = [False]

        def clear_starts_then_stop() -> None:
            clear_starts()
            if not stopped[0]:
                stopped[0] = True
                monitor.remove_listener(pause_listener)
                monitor.release()

        monkeypatch.setattr(monitor, "_clear_starts", clear_starts_then_stop)

        assert _finishes(monitor.reset)
        assert stopped[0]
        _assert_stopped(monitor, pause_listener)


def test_stop_does_not_wait_for_another_thread_that_holds_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The holder applies a stop that another thread queued, when it unlocks."""
    monitor: GCPauseMonitor
    pause_listener: PauseListener
    with _acquired_monitor_with_listener() as (monitor, pause_listener):
        clear_starts: Callable[[], None] = monitor._clear_starts
        holding: threading.Event = threading.Event()
        proceed: threading.Event = threading.Event()

        def clear_starts_and_hold() -> None:
            clear_starts()
            if not holding.is_set():
                holding.set()
                proceed.wait(timeout=2)

        monkeypatch.setattr(monitor, "_clear_starts", clear_starts_and_hold)
        holder: threading.Thread = threading.Thread(target=monitor.reset, daemon=True)
        holder.start()
        try:
            assert holding.wait(timeout=5)
            monitor.remove_listener(pause_listener)
            monitor.release()
            assert holder.is_alive() and not proceed.is_set(), "stop waited for the other thread"
            assert monitor._refcount == 1
        finally:
            proceed.set()
            holder.join(timeout=5)

        assert not holder.is_alive()
        _assert_stopped(monitor, pause_listener)


@pytest.mark.parametrize("applied_by", ["release", "_on_gc"])
def test_stop_does_not_wait_for_a_thread_that_takes_the_lock_after_it_is_applied(applied_by: str) -> None:
    """Once the last release is applied, applying it must not take the lock again.

    Another thread can enter reset() or snapshot_and_reset() right then. The stop is
    applied by release() itself, or by a GC callback that held the lock when it landed.
    """
    monitor: GCPauseMonitor
    pause_listener: PauseListener
    with _acquired_monitor_with_listener() as (monitor, pause_listener):
        raw_lock: Any = monitor._lock
        lock: _LockWithStop = _LockWithStop(raw_lock)
        go: threading.Event = threading.Event()
        taken: threading.Event = threading.Event()
        let_go: threading.Event = threading.Event()

        def stop() -> None:
            monitor.remove_listener(pause_listener)
            monitor.release()

        def collect() -> None:
            monitor._on_gc("stop", {"generation": 0})

        def hold() -> None:
            go.wait(timeout=10)
            with raw_lock:
                taken.set()
                let_go.wait(timeout=10)

        def take_the_lock_once_stopped() -> None:
            if monitor._refcount != 0:
                lock.after_release = take_the_lock_once_stopped
                return
            go.set()
            taken.wait(timeout=5)

        section: Callable[[], None] = stop
        if applied_by == "_on_gc":
            lock.after_acquire = stop
            section = collect
        lock.after_release = take_the_lock_once_stopped
        monitor._lock = lock  # type: ignore[assignment]
        holder: threading.Thread = threading.Thread(target=hold, daemon=True)
        holder.start()
        try:
            assert _finishes(section), "the stop waited for the thread that took the lock"
            assert taken.is_set()
        finally:
            go.set()
            let_go.set()
            holder.join(timeout=5)

        _assert_stopped(monitor, pause_listener)


def test_acquire_right_after_the_last_release_is_applied_stays_installed() -> None:
    """The last release must not remove what an acquire on another thread installs right after it."""
    monitor: GCPauseMonitor
    pause_listener: PauseListener
    with _acquired_monitor_with_listener() as (monitor, pause_listener):
        lock: _LockWithStop = _LockWithStop(monitor._lock)
        acquired: list[bool] = []

        def acquire_once_released() -> None:
            if monitor._refcount != 0:
                lock.after_release = acquire_once_released
                return
            acquired.append(_finishes(monitor.acquire))

        lock.after_release = acquire_once_released
        monitor._lock = lock  # type: ignore[assignment]
        monitor.remove_listener(pause_listener)
        monitor.release()

        assert acquired == [True]
        assert monitor._refcount == 1
        assert [cb for cb in gc.callbacks if cb is monitor._gc_hook] == [monitor._gc_hook]
        assert forksafe._registry.count(monitor._fork_hook) == 1
        monitor.release()
        _assert_stopped(monitor, pause_listener)


@pytest.mark.parametrize("step", ["_remove_gc_callback", "unregister"])
def test_collection_during_uninstall_does_not_wait_for_the_lock(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    """The last release uninstalls while it holds the lock, and a collection can start in there.

    CPython can start one on this thread at an allocation or at the next eval-breaker
    check. A callback that is still installed then runs on the thread that holds the lock:
    it must drop the sample and the start it had for that generation, and not wait.
    """
    monitor: GCPauseMonitor = GCPauseMonitor()
    target: Any = gc_monitor if step == "_remove_gc_callback" else forksafe
    uninstall_step: Callable[[Any], None] = getattr(target, step)
    during: list[tuple[bool, bool, int, int]] = []

    def collect_then_uninstall(hook: Any) -> None:
        installed: bool = any(cb is monitor._gc_hook for cb in gc.callbacks)
        gc.collect()
        during.append((monitor._lock.locked(), installed, monitor._start_ns[2], monitor._count))
        uninstall_step(hook)

    with _automatic_gc_disabled():
        monitor.acquire()
        try:
            monitor._on_gc("start", {"generation": 2})
            start: int = monitor._start_ns[2]
            with monkeypatch.context() as patch:
                patch.setattr(target, step, collect_then_uninstall)
                assert _finishes(monitor.release), "a collection during uninstall waited for the lock"
        finally:
            gc_monitor._remove_gc_callback(monitor._gc_hook)
            if monitor._fork_hook in forksafe._registry:
                forksafe.unregister(monitor._fork_hook)

    still_installed: bool = step == "_remove_gc_callback"
    assert during == [(True, still_installed, 0 if still_installed else start, 0)]
    assert monitor._refcount == 0
    assert all(cb is not monitor._gc_hook for cb in gc.callbacks)
    assert monitor._fork_hook not in forksafe._registry


def test_install_and_uninstall_do_not_wait_for_forksafe() -> None:
    """acquire() and the last release register and unregister the fork hook while they hold the lock.

    forksafe holds its fork lock while it runs the before-fork and after-fork-in-parent
    hooks. One before-fork hook joins the periodic threads, the runtime metrics worker among
    them, and that worker takes the monitor lock when it flushes. Waiting for the fork lock
    here would deadlock with that join.
    """
    monitor: GCPauseMonitor = GCPauseMonitor()
    holding: threading.Event = threading.Event()
    let_go: threading.Event = threading.Event()

    def fork_in_progress() -> None:
        with forksafe._fork_lock:
            holding.set()
            let_go.wait(timeout=10)

    forking: threading.Thread = threading.Thread(target=fork_in_progress, daemon=True)
    forking.start()
    try:
        assert holding.wait(timeout=5)
        assert _finishes(monitor.acquire), "acquire() waited for the fork lock"
        assert monitor._fork_hook in forksafe._registry
        assert _finishes(monitor.release), "release() waited for the fork lock"
    finally:
        let_go.set()
        forking.join(timeout=5)
        gc_monitor._remove_gc_callback(monitor._gc_hook)
        if monitor._fork_hook in forksafe._registry:
            forksafe.unregister(monitor._fork_hook)

    assert monitor._refcount == 0
    assert all(cb is not monitor._gc_hook for cb in gc.callbacks)
    assert monitor._fork_hook not in forksafe._registry


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
