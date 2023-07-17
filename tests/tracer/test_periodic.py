import threading
from threading import Event
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
    assert t.is_alive()
    t.stop()
    t.join()
    assert not t.is_alive()
    assert x["OK"]
    assert x["DOWN"]
    if hasattr(threading, "get_native_id"):
        assert t.native_id is not None


def test_periodic_double_start():
    def _run_periodic():
        pass

    t = periodic.PeriodicThread(0.1, _run_periodic)
    t.start()
    with pytest.raises(RuntimeError):
        t.start()


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


def test_is_alive_before_start():
    def x():
        pass

    t = periodic.PeriodicThread(1, x)
    assert not t.is_alive()


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

    # Sleep long enough to also trigger the periodic function with the timeout
    sleep(1.1 * interval)

    awake_me.stop()

    assert queue == list(range(n + 2))
