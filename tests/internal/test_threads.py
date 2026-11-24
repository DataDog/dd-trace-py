import errno
from threading import Event
from threading import Thread as StdThread
from time import monotonic
from time import sleep
from unittest import mock

import pytest

from ddtrace.internal import threads


def test_thread_runs_target_exactly_once():
    calls = []

    t = threads.Thread(target=lambda: calls.append(1), name="test-thread-once")
    t.start()
    t.join()

    assert calls == [1]

    # No periodic re-invocation after the one-shot run completes.
    sleep(0.05)
    assert calls == [1]


def test_thread_autorestart_is_false():
    assert threads.Thread.__autorestart__ is False
    assert threads.PeriodicThread.__autorestart__ is True


def test_thread_stops_after_target_raises():
    calls = []

    def target():
        calls.append(1)
        raise ValueError("boom")

    t = threads.Thread(target=target, name="test-thread-raises")
    t.start()
    t.join()

    assert calls == [1]

    # The exception path still terminates the thread for good; no retry.
    sleep(0.05)
    assert calls == [1]


def test_thread_before_fork_joins_and_child_does_not_restart():
    started = Event()
    release = Event()
    calls = []

    def target():
        started.set()
        release.wait(timeout=5)
        calls.append(1)

    t = threads.Thread(target=target, name="test-thread-before-fork-child")
    t.start()
    assert started.wait(timeout=5), "thread did not start in time"

    releaser = StdThread(target=lambda: (sleep(0.05), release.set()))
    releaser.start()
    try:
        # _before_fork() must block until the one-shot target has completed.
        threads._before_fork()
        assert calls == [1]

        # The child must not restart a one-shot thread.
        threads._after_fork_child()
        sleep(0.05)
        assert calls == [1]
    finally:
        releaser.join(timeout=5)
        threads._threads_to_restart_after_fork.clear()
        threads._forking = False


def test_thread_before_fork_joins_and_parent_does_not_restart():
    started = Event()
    release = Event()
    calls = []

    def target():
        started.set()
        release.wait(timeout=5)
        calls.append(1)

    t = threads.Thread(target=target, name="test-thread-before-fork-parent")
    t.start()
    assert started.wait(timeout=5), "thread did not start in time"

    releaser = StdThread(target=lambda: (sleep(0.05), release.set()))
    releaser.start()
    try:
        threads._before_fork()
        assert calls == [1]

        # The parent schedules a ThreadRestartTimer since there is a pending
        # restart entry, but Thread instances must be explicitly skipped.
        threads._after_fork_parent()

        # Wait comfortably past the timer's 100ms interval.
        sleep(0.3)
        assert calls == [1]
    finally:
        releaser.join(timeout=5)
        threads.ThreadRestartTimer.clear()
        threads._threads_to_restart_after_fork.clear()
        threads._forking = False


def test_before_fork_stops_waiting_for_a_thread_that_does_not_stop(monkeypatch) -> None:
    """os.fork() runs on an application thread, so a thread that does not stop must delay it, not hang it."""
    monkeypatch.setattr(threads, "_FORK_JOIN_TIMEOUT_NS", int(1e9))
    entered: Event = Event()
    release: Event = Event()
    calls: list[int] = []

    def target() -> None:
        calls.append(1)
        entered.set()
        # No timeout, on purpose: the bound is for targets that break that rule.
        release.wait()

    t: threads.PeriodicThread = threads.PeriodicThread(0.01, target, name="test-stuck-before-fork")
    t.start()
    assert entered.wait(timeout=5), "thread did not start in time"

    returned: Event = Event()

    def before_fork() -> None:
        threads._before_fork()
        returned.set()

    forker: StdThread = StdThread(target=before_fork, daemon=True)
    try:
        with mock.patch.object(threads.log, "warning") as warning:
            start: float = monotonic()
            forker.start()
            # Without the bound, _before_fork() waits for release forever: fail here instead of hanging.
            assert returned.wait(timeout=5), "_before_fork() kept waiting for a thread that does not stop"
            assert monotonic() - start >= 0.9
        assert [call.args[1] for call in warning.call_args_list] == ["test-stuck-before-fork"]

        # Restarting the thread now would add a second worker next to the blocked one.
        threads._after_fork_parent()
        sleep(0.3)
        assert calls == [1]

        # Once the blocked worker stops, the parent restarts the thread.
        release.set()
        for _ in range(100):
            if len(calls) > 1:
                break
            sleep(0.05)
        assert len(calls) > 1
    finally:
        release.set()
        forker.join(timeout=5)
        t.stop()
        t.join(timeout=5)
        threads.ThreadRestartTimer.clear()
        threads._threads_to_restart_after_fork.clear()
        threads._forking = False


@pytest.mark.subprocess(err=lambda err: err.count("did not stop within") == 1)
def test_fork_goes_ahead_without_a_thread_that_does_not_stop() -> None:
    """A real os.fork() returns while a thread does not stop. The child, which does not have that thread, restarts
    it, and later forks from two threads do not wait for it again.
    """
    import os
    from threading import Event
    from threading import Thread as StdThread
    from time import monotonic

    from ddtrace.internal import threads

    threads._FORK_JOIN_TIMEOUT_NS = int(1e9)

    entered: Event = Event()
    release: Event = Event()

    def target() -> None:
        entered.set()
        # No timeout, on purpose: the bound is for targets that break that rule.
        release.wait()

    t: threads.PeriodicThread = threads.PeriodicThread(0.01, target, name="test-stuck-fork")
    t.start()
    assert entered.wait(timeout=5), "thread did not start in time"
    entered.clear()

    forked: Event = Event()

    def watchdog() -> None:
        # Without the bound, os.fork() never returns: fail here rather than at the subprocess timeout.
        if not forked.wait(timeout=10):
            os._exit(42)

    StdThread(target=watchdog, daemon=True).start()

    try:
        pid: int = os.fork()
        if pid == 0:
            release.set()
            os._exit(0 if entered.wait(timeout=5) else 12)
        forked.set()
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0, "the child did not restart the thread"

        def fork_three_times() -> None:
            for _ in range(3):
                child: int = os.fork()
                if child == 0:
                    os._exit(0)
                os.waitpid(child, 0)

        forkers: list[StdThread] = [StdThread(target=fork_three_times) for _ in range(2)]
        start: float = monotonic()
        for forker in forkers:
            forker.start()
        for forker in forkers:
            forker.join()
        # Waiting for the thread again would take 1s per fork.
        assert monotonic() - start < 3, "later forks waited for the thread again"

        # The parent restarts the thread only once its blocked worker has stopped.
        assert not entered.is_set()
        release.set()
        assert entered.wait(timeout=5), "the parent did not restart the thread"
    finally:
        # The exit hook joins periodic threads without a timeout, so a failed
        # assertion must not leave the thread blocked.
        release.set()
    t.stop()
    t.join(timeout=5)


@pytest.mark.subprocess()
def test_thread_stopped_right_after_start_still_runs_target():
    # A stop landing between start() and the first call used to skip the target,
    # and the parent's restart logic, which assumes one-shot threads have run,
    # never ran it. The worker could only lose that race if the OS descheduled it
    # after it released the GIL, so pin the process to one CPU and keep it busy
    # with threads that burn CPU without the GIL.
    import hashlib
    import os
    from threading import Event
    from threading import Thread as StdThread

    from ddtrace.internal import threads

    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {next(iter(os.sched_getaffinity(0)))})

    done = Event()
    data = b"x" * (1 << 20)

    def burn():
        while not done.is_set():
            hashlib.sha256(data).digest()

    burners = [StdThread(target=burn, daemon=True) for _ in range(4)]
    for b in burners:
        b.start()

    missed = 0
    try:
        for _ in range(2000):
            calls = []
            t = threads.Thread(target=lambda: calls.append(1), name="test-thread-stop-after-start")
            t.start()
            t._before_fork()
            t.join()
            missed += not calls
    finally:
        done.set()
        for b in burners:
            b.join()

    assert missed == 0, missed


def test_thread_queued_start_during_fork_does_not_run_in_child():
    calls = []

    t = threads.Thread(target=lambda: calls.append(1), name="test-thread-queued-child")

    original_forking = threads._forking
    threads._forking = True
    try:
        t.start()
        assert calls == []

        threads._forking = False
        threads._after_fork_child()
        sleep(0.05)
        assert calls == []
    finally:
        threads._forking = original_forking
        threads._threads_to_start_after_fork.clear()


def test_thread_queued_start_during_fork_runs_once_in_parent():
    calls = []

    t = threads.Thread(target=lambda: calls.append(1), name="test-thread-queued-parent")

    original_forking = threads._forking
    threads._forking = True
    try:
        t.start()
        assert calls == []

        threads._forking = False
        threads._after_fork_parent()

        for _ in range(50):
            if calls:
                break
            sleep(0.05)

        assert calls == [1]
        sleep(0.05)
        assert calls == [1]
    finally:
        threads._forking = original_forking
        threads.ThreadRestartTimer.clear()
        threads._threads_to_start_after_fork.clear()


def test_periodic_thread_cancel_if_not_started():
    t = threads.PeriodicThread(60.0, lambda: None, name="test-periodic-cancel")
    assert t.cancel_if_not_started() is True

    t.start()
    try:
        assert t.cancel_if_not_started() is False
    finally:
        t.stop()
        t.join()


def test_periodic_thread_cancel_if_not_started_drops_start_queued_during_fork():
    calls = []

    t = threads.PeriodicThread(0.01, lambda: calls.append(1), name="test-periodic-cancel-queued")

    original_forking = threads._forking
    threads._forking = True
    try:
        t.start()
        assert t.cancel_if_not_started() is True
        assert all(start.__self__ is not t for start in threads._threads_to_start_after_fork)

        threads._forking = False
        threads._after_fork_parent()
        sleep(0.3)

        assert calls == []
        assert t.is_started() is False
    finally:
        threads._forking = original_forking
        threads.ThreadRestartTimer.clear()
        threads._threads_to_start_after_fork.clear()


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_thread_identity_follows_the_application_threading_module():
    """Thread identities come from the copy of threading the application imports.

    With module cloning, only that copy knows the threads the application
    starts, e.g. their names. Before the application imports it, the identities
    come from the copy ddtrace was loaded with, without importing threading.
    """
    import sys

    import ddtrace.auto  # noqa:F401
    from ddtrace.internal import threads

    assert threads.is_main_thread()
    assert "threading" not in sys.modules

    import threading

    names = []
    worker = threading.Thread(target=lambda: names.append(threads.current_thread().name), name="app-worker")
    worker.start()
    worker.join()

    assert names == ["app-worker"]
    assert threads.current_thread() is threading.current_thread()
    assert threads.is_main_thread()


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_main_thread_is_the_one_that_loaded_ddtrace():
    """The application's copy of threading, if first imported off the main
    thread, takes that thread as the main one on Python < 3.13. That must not
    stop ddtrace from recognising the actual main thread, e.g. to install signal
    handlers.
    """
    import _thread
    import sys

    import ddtrace.auto  # noqa:F401
    from ddtrace.internal import threads

    assert "threading" not in sys.modules

    imported = _thread.allocate_lock()
    imported.acquire()
    is_main = []

    def import_threading():
        import threading  # noqa:F401

        is_main.append(threads.is_main_thread())
        imported.release()

    _thread.start_new_thread(import_threading, ())
    imported.acquire()

    assert is_main == [False]
    assert threads.is_main_thread()


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_local_and_event_follow_gevent_patching():
    """Thread-local storage and events come from the application's copy of
    threading as patched by gevent, so they are greenlet-local and cooperative.
    The originals still refer to OS threads.
    """
    import gevent.monkey

    import ddtrace.auto  # noqa:F401

    gevent.monkey.patch_all()

    import gevent
    import gevent.event
    import gevent.local

    from ddtrace.internal import threads

    assert isinstance(threads.local(), gevent.local.local)
    assert not isinstance(threads.original_local(), gevent.local.local)

    storage = threads.local()

    def set_and_get(value):
        storage.value = value
        gevent.sleep(0)
        return storage.value

    greenlets = [gevent.spawn(set_and_get, i) for i in range(2)]
    gevent.joinall(greenlets)
    assert [g.value for g in greenlets] == [0, 1]

    # A wait on the event yields to other greenlets instead of blocking the hub.
    event = threads.Event()
    setter = gevent.spawn(event.set)
    assert event.wait(timeout=5)
    setter.join()


def test_restart_failures_are_logged_after_releasing_forking_lock(monkeypatch):
    """A log handler can wait for a lock whose holder waits for _forking_lock, so log restart errors after it."""

    class FailingPeriodicThread:
        name = "failing-restart"
        ident = None

        def _after_fork(self, force):
            raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

    class FailingQueuedThread:
        name = "failing-start"
        __autorestart__ = True

        def start(self):
            raise OSError(errno.ENOMEM, "Cannot allocate memory")

    logged = []
    monkeypatch.setattr(threads.ThreadRestartTimer, "_timestamp", 0)
    threads._threads_to_restart_after_fork.add(FailingPeriodicThread())
    threads._threads_to_start_after_fork.append(FailingQueuedThread().start)
    try:
        with mock.patch.object(
            threads.log,
            "error",
            side_effect=lambda msg, name, error: logged.append((msg, name, threads._forking_lock.locked())),
        ):
            threads.ThreadRestartTimer(force_restart=True)._restart_threads()
    finally:
        threads._threads_to_restart_after_fork.clear()
        threads._threads_to_start_after_fork.clear()

    assert logged == [
        ("failed to restart periodic thread %s after fork: %s", "failing-restart", False),
        ("failed to start periodic thread %s: %s", "failing-start", False),
    ]
