import pytest


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_CAPTURE_PCT="100"),
    err=None,
)
def test_lock_acquire_events():
    import threading

    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import threading as collector_threading
    from tests.profiling.collector import _asyncio_compat

    async def _lock():
        lock = threading.Lock()
        lock.acquire()

    def asyncio_run():
        lock = threading.Lock()
        lock.acquire()
        _asyncio_compat.run(_lock())

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
        if event.lock_name == "test_threading_asyncio.py:16:lock":
            assert event.task_name.startswith("Task-")
            lock_found += 1
        elif event.lock_name == "test_threading_asyncio.py:20:lock":
            assert event.task_name is None
            assert event.thread_name == "foobar"
            lock_found += 1

    assert lock_found == 2, lock_found
