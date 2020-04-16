import os
import threading

import pytest

from ddtrace.profiling import _periodic


def test_periodic():
    x = {"OK": False}

    def _run_periodic():
        x["OK"] = True

    def _on_shutdown():
        x["DOWN"] = True

    t = _periodic.PeriodicRealThread(0.001, _run_periodic, on_shutdown=_on_shutdown)
    t.start()
    assert t.ident in _periodic.PERIODIC_THREAD_IDS
    while not x["OK"]:
        pass
    t.stop()
    t.join()
    assert x["OK"]
    assert x["DOWN"]
    assert t.ident not in _periodic.PERIODIC_THREAD_IDS


def test_periodic_error():
    x = {"OK": False}

    def _run_periodic():
        x["OK"] = True
        raise ValueError

    def _on_shutdown():
        x["DOWN"] = True

    t = _periodic.PeriodicRealThread(0.001, _run_periodic, on_shutdown=_on_shutdown)
    t.start()
    assert t.ident in _periodic.PERIODIC_THREAD_IDS
    while not x["OK"]:
        pass
    t.stop()
    t.join()
    assert "DOWN" not in x
    assert t.ident not in _periodic.PERIODIC_THREAD_IDS


def test_gevent_class():
    if os.getenv("DD_PROFILE_TEST_GEVENT", False):
        assert isinstance(_periodic.PeriodicRealThread(1, sum), _periodic._GeventPeriodicThread)
    else:
        assert isinstance(_periodic.PeriodicRealThread(1, sum), _periodic.PeriodicThread)


def test_periodic_real_thread_name():
    def do_nothing():
        pass

    t = _periodic.PeriodicRealThread(interval=1, target=do_nothing)
    t.start()
    assert t in threading.enumerate()
    t.stop()
    t.join()


def test_periodic_service_start_stop():
    t = _periodic.PeriodicService(1)
    t.start()
    with pytest.raises(RuntimeError):
        t.start()
    t.stop()
    t.join()
    t.stop()
    t.stop()
    t.join()
    t.join()


def test_periodic_join_stop_no_start():
    t = _periodic.PeriodicService(1)
    t.join()
    t.stop()
    t.join()
    t = _periodic.PeriodicService(1)
    t.stop()
    t.join()
    t.stop()
