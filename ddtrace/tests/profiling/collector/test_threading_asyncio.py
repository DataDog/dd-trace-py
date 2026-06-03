import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_CAPTURE_PCT="100",
        DD_PROFILING_OUTPUT_PPROF="/tmp/asyncio_lock_acquire_events",
        DD_PROFILING_FILE_PATH=__file__,
    ),
    err=None,
)
def test_lock_acquire_events():
    import os
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    from tests.profiling.collector.test_utils import async_run

    # Tests that lock acquire/release events from asyncio threads are captured
    # correctly. See test_asyncio.py for asyncio.Lock tests.
    async def _lock():
        lock = threading.Lock()  # !CREATE! test_lock_acquire_events_1
        lock.acquire()  # !ACQUIRE! test_lock_acquire_events_1
        lock.release()  # !RELEASE! test_lock_acquire_events_1

    def asyncio_run_func():
        lock = threading.Lock()  # !CREATE! test_lock_acquire_events_2
        lock.acquire()  # !ACQUIRE! test_lock_acquire_events_2
        async_run(_lock())
        lock.release()  # !RELEASE! test_lock_acquire_events_2

    # start a complete profiler so asyncio policy is setup
    p = profiler.Profiler()
    p.start()
    t = threading.Thread(target=asyncio_run_func, name="foobar")
    t.start()
    t.join()
    p.stop()

    expected_filename = "test_threading_asyncio.py"
    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    linenos_1 = get_lock_linenos("test_lock_acquire_events_1")
    linenos_2 = get_lock_linenos("test_lock_acquire_events_2")

    profile = pprof_utils.parse_newest_profile(output_filename)

    task_name_regex = r"Task-\d+$"

    # uvloop wraps coroutine execution, so the lock acquire/release inside the coroutine
    # is reported from uvloop's wrapper code, not from _lock. We skip the _lock lock
    # event check when using uvloop since the filename and line numbers would be from
    # uvloop's source code.
    use_uvloop = os.environ.get("USE_UVLOOP", "0") == "1"

    expected_acquire_events = [
        pprof_utils.LockAcquireEvent(
            caller_name="asyncio_run_func",
            filename=expected_filename,
            linenos=linenos_2,
            lock_name="lock",
            thread_name="foobar",
        ),
    ]
    expected_release_events = [
        pprof_utils.LockReleaseEvent(
            caller_name="asyncio_run_func",
            filename=expected_filename,
            linenos=linenos_2,
            lock_name="lock",
            thread_name="foobar",
        ),
    ]

    if not use_uvloop:
        expected_acquire_events.append(
            pprof_utils.LockAcquireEvent(
                caller_name="_lock",
                filename=expected_filename,
                linenos=linenos_1,
                lock_name="lock",
                task_name=task_name_regex,
                thread_name="foobar",
            )
        )
        expected_release_events.append(
            pprof_utils.LockReleaseEvent(
                caller_name="_lock",
                filename=expected_filename,
                linenos=linenos_1,
                lock_name="lock",
                task_name=task_name_regex,
                thread_name="foobar",
            )
        )

    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=expected_acquire_events,
        expected_release_events=expected_release_events,
    )
