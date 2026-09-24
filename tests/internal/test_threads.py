from threading import Event
from threading import Thread as StdThread
from time import sleep

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
