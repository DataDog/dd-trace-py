from threading import Event
from threading import Thread as StdThread
from time import sleep

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

    assert threads.current_thread() is threads.main_thread()
    assert "threading" not in sys.modules

    import threading

    names = []
    worker = threading.Thread(target=lambda: names.append(threads.current_thread().name), name="app-worker")
    worker.start()
    worker.join()

    assert names == ["app-worker"]
    assert threads.current_thread() is threading.current_thread()
    assert threads.main_thread() is threading.main_thread()
