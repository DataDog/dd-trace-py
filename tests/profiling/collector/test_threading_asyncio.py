import os
import threading

import pytest

from ddtrace.profiling import profiler
from ddtrace.profiling.collector import threading as collector_threading

from . import _asyncio_compat


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


@pytest.mark.skipif(not _asyncio_compat.PY36_AND_LATER, reason="Python > 3.5 needed")
def test_lock_acquire_events(tmp_path, monkeypatch):
    async def _lock():
        lock = threading.Lock()
        lock.acquire()

    def asyncio_run():
        lock = threading.Lock()
        lock.acquire()
        _asyncio_compat.run(_lock())

    monkeypatch.setenv("DD_PROFILING_CAPTURE_PCT", "100")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", str(tmp_path / "pprof"))
    # start a complete profiler so asyncio policy is setup
    p = profiler.Profiler()
    p.start()
    t = threading.Thread(target=asyncio_run, name="foobar")
    t.start()
    t.join()
    events = p._profiler._recorder.reset()
    p.stop()

    lock_found = 0
    for event in events[collector_threading.ThreadingLockAcquireEvent]:
        if event.lock_name == "test_threading_asyncio.py:18":
            assert event.task_name.startswith("Task-")
            lock_found += 1
        elif event.lock_name == "test_threading_asyncio.py:22":
            if TESTING_GEVENT:
                assert event.task_name == "foobar"
                assert event.thread_name == "MainThread"
            else:
                assert event.task_name is None
                assert event.thread_name == "foobar"
            lock_found += 1

    if lock_found != 2:
        pytest.fail("Lock events not found")
