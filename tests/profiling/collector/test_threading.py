import os
import sys
import threading

import pytest

from ddtrace.profiling.collector import threading as collector_threading

from . import test_collector
from .lock_utils import init_linenos


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)

init_linenos(__file__)


def test_repr():
    test_collector._test_repr(
        collector_threading.ThreadingLockCollector,
        "ThreadingLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "capture_pct=1.0, nframes=64, "
        "endpoint_collection_enabled=True, tracer=None)",
    )


@pytest.mark.benchmark(
    group="threading-lock-create",
)
def test_lock_create_speed(benchmark):
    benchmark(threading.Lock)


def _lock_acquire_release(lock):
    lock.acquire()
    lock.release()


@pytest.mark.benchmark(
    group="threading-lock-acquire-release",
)
def test_lock_acquire_release_speed(benchmark):
    benchmark(_lock_acquire_release, threading.Lock())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="only works on linux")
@pytest.mark.skipif(sys.version_info[:2] == (3, 9), reason="This test is flaky on Python 3.9")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="true"),
    err=None,
)
def test_user_threads_have_native_id():
    from os import getpid
    from threading import Thread
    from threading import _MainThread
    from threading import current_thread
    from time import sleep

    main = current_thread()
    assert isinstance(main, _MainThread)
    # We expect the main thread to have the same ID as the PID
    assert main.native_id == getpid(), (main.native_id, getpid())

    t = Thread(target=lambda: None)
    t.start()

    for _ in range(10):
        try:
            # The TID should be higher than the PID, but not too high
            assert 0 < t.native_id - getpid() < 100, (t.native_id, getpid())
        except AttributeError:
            # The native_id attribute is set by the thread so we might have to
            # wait a bit for it to be set.
            sleep(0.1)
        else:
            break
    else:
        raise AssertionError("Thread.native_id not set")

    t.join()


class Foo:
    def __init__(self):
        self.foo_lock = threading.Lock()  # !CREATE! foolock

    def foo(self):
        with self.foo_lock:  # !RELEASE! !ACQUIRE! foolock
            pass


class Bar:
    def __init__(self):
        self.foo = Foo()

    def bar(self):
        self.foo.foo()
