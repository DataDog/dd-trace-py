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


@pytest.mark.subprocess(timeout=30)
def test_thread_started_with_boot_gate_closed_runs_once_it_opens():
    from ddtrace.internal import threads

    calls = []
    threads.boot_gate.clear()

    t = threads.Thread(target=lambda: calls.append(1), name="test-thread-gated")
    t.start()
    # The worker holds the GIL from its start to its target, unless it waits on the gate.
    assert calls == []

    threads.boot_gate.set()
    t.join()
    assert calls == [1]


@pytest.mark.subprocess(timeout=30)
def test_boot_gate_is_open_by_default():
    from ddtrace.internal import threads

    assert threads.boot_gate.is_set()

    ran = threads.original_Event()
    t = threads.Thread(target=ran.set, name="test-thread-ungated")
    t.start()
    assert ran.wait(10)
    t.join()


@pytest.mark.subprocess(timeout=30)
def test_stop_and_join_with_boot_gate_closed_do_not_run_target():
    from ddtrace.internal import threads

    calls = []
    threads.boot_gate.clear()

    periodic = threads.PeriodicThread(
        0.01, lambda: calls.append("periodic"), name="test-gated-periodic", no_wait_at_start=True
    )
    one_shot = threads.Thread(target=lambda: calls.append("one-shot"), name="test-gated-one-shot")
    periodic.start()
    one_shot.start()

    periodic.stop()
    one_shot.stop()
    periodic.join()
    one_shot.join()

    assert threads.boot_gate.is_set()
    assert calls == []


@pytest.mark.subprocess(timeout=30)
def test_exit_with_boot_gate_closed_does_not_hang_or_run_target():
    from ddtrace.internal import threads

    threads.boot_gate.clear()

    t = threads.PeriodicThread(0.01, lambda: print("ran"), name="test-gated-at-exit", no_wait_at_start=True)
    t.start()


@pytest.mark.subprocess(timeout=30)
def test_fork_with_boot_gate_closed_releases_threads():
    import os

    from ddtrace.internal import threads

    calls = []
    threads.boot_gate.clear()

    def record(name):
        return lambda: calls.append((name, threads._forking))

    periodic = threads.PeriodicThread(60, record("periodic"), name="test-gated-fork", no_wait_at_start=True)
    one_shot = threads.Thread(target=record("one-shot"), name="test-gated-fork-one-shot")
    periodic.start()
    one_shot.start()

    pid = os.fork()
    if pid == 0:
        # The child inherits the gate open.
        os._exit(0 if threads.boot_gate.is_set() else 1)

    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    assert threads.boot_gate.is_set()

    # The periodic thread stopped for the fork without running, while the one-shot thread, which is not restarted
    # after it, ran before it.
    assert ("periodic", True) not in calls, calls
    assert ("one-shot", True) in calls, calls


@pytest.mark.subprocess(timeout=30)
def test_stopped_periodic_thread_is_freed_without_gc():
    import gc
    import weakref

    from ddtrace.internal import threads

    gc.disable()

    t = threads.PeriodicThread(60, lambda: None, name="test-freed-without-gc", no_wait_at_start=True)
    t.start()
    t.stop()
    t.join()

    ref = weakref.ref(t)
    del t
    assert ref() is None
