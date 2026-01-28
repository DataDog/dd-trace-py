import os
from threading import Event
from time import monotonic
from time import sleep

import pytest

from ddtrace.internal import periodic
from ddtrace.internal import service


def test_periodic():
    x = {"OK": False}

    thread_started = Event()
    thread_continue = Event()

    def _run_periodic():
        thread_started.set()
        x["OK"] = True
        thread_continue.wait()

    def _on_shutdown():
        x["DOWN"] = True

    t = periodic.PeriodicThread(0.001, _run_periodic, on_shutdown=_on_shutdown)
    t.start()
    thread_started.wait()
    thread_continue.set()
    t.stop()
    t.join()
    assert x["OK"]
    assert x["DOWN"]


def test_periodic_double_start():
    def _run_periodic():
        pass

    t = periodic.PeriodicThread(0.1, _run_periodic)
    t.start()
    with pytest.raises(RuntimeError):
        t.start()
    t.stop()
    t.join()


def test_periodic_error():
    x = {"OK": False}

    thread_started = Event()
    thread_continue = Event()

    def _run_periodic():
        thread_started.set()
        thread_continue.wait()
        raise ValueError

    def _on_shutdown():
        x["DOWN"] = True

    t = periodic.PeriodicThread(0.001, _run_periodic, on_shutdown=_on_shutdown)
    t.start()
    thread_started.wait()
    thread_continue.set()
    t.stop()
    t.join()
    assert "DOWN" not in x


def test_periodic_service_start_stop():
    t = periodic.PeriodicService(1)
    t.start()
    with pytest.raises(service.ServiceStatusError):
        t.start()
    t.stop()
    t.join()
    with pytest.raises(service.ServiceStatusError):
        t.stop()
    with pytest.raises(service.ServiceStatusError):
        t.stop()
    t.join()
    t.join()


def test_periodic_join_stop_no_start():
    t = periodic.PeriodicService(1)
    t.join()
    with pytest.raises(service.ServiceStatusError):
        t.stop()
    t.join()
    t = periodic.PeriodicService(1)
    with pytest.raises(service.ServiceStatusError):
        t.stop()
    t.join()
    with pytest.raises(service.ServiceStatusError):
        t.stop()


def test_awakeable_periodic_service():
    queue = []

    class AwakeMe(periodic.AwakeablePeriodicService):
        def periodic(self):
            queue.append(len(queue))

    interval = 1

    awake_me = AwakeMe(interval)

    awake_me.start()

    # Manually awake the service
    n = 10
    for _ in range(10):
        awake_me.awake()

    assert queue == list(range(n))

    # Sleep long enough to also trigger the periodic function with the timeout
    sleep(1.1 * interval)

    awake_me.stop()

    assert queue == list(range(n + 1))


def test_forksafe_awakeable_periodic_service():
    queue = [None]
    periodic_ran = Event()

    class AwakeMe(periodic.ForksafeAwakeablePeriodicService):
        def reset(self):
            queue.clear()
            periodic_ran.clear()

        def periodic(self):
            queue.append(len(queue))
            periodic_ran.set()

    awake_me = AwakeMe(1)
    awake_me.start()

    assert queue

    pid = os.fork()
    if pid == 0:
        # child: check that the thread has been restarted and the state has been
        # reset
        assert not queue

        awake_me.awake()
        periodic_ran.wait(timeout=5)  # Wait for periodic() to complete
        assert queue
        os._exit(42)

    awake_me.stop()

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42


def test_timer():
    end = 0

    class TestTimer(periodic.Timer):
        def timeout(self):
            nonlocal end
            end = monotonic()

    T = 0.1
    t = TestTimer(T)

    start = monotonic()
    t.start()
    t.join()

    assert end >= start + T


def test_timer_reset():
    end = count = 0

    class TestTimer(periodic.Timer):
        def timeout(self):
            nonlocal end, count

            count += 1
            end = monotonic()

    T = 0.1
    t = TestTimer(T)

    start = monotonic()
    t.start()

    for _ in range(N := 5):
        sleep(T / 2)
        t.reset()

    t.join()

    assert count == 1, "timed out once"
    assert end >= start + (N * T / 2) + T
