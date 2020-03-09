import os
import threading

import pytest

from ddtrace.profile import _periodic


def _test_periodic(thread_class):
    x = {"OK": False}

    def _run_periodic():
        x["OK"] = True

    t = thread_class(0.001, _run_periodic)
    t.start()
    assert t.ident in _periodic.PERIODIC_THREAD_IDS
    while not x["OK"]:
        pass
    t.stop()
    t.join()
    assert x["OK"]
    assert len(_periodic.PERIODIC_THREAD_IDS) == 0


@pytest.mark.skipif(os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="gevent threads are not running in parallel")
def test_periodic_thread():
    return _test_periodic(_periodic.PeriodicThread)


def test_periodic_real_thread():
    return _test_periodic(_periodic.PeriodicRealThread)


def test_periodic_real_thread_name():
    def do_nothing():
        pass

    t = _periodic.PeriodicRealThread(interval=1, target=do_nothing)
    t.start()
    assert t in threading.enumerate()
    t.stop()
    t.join()
