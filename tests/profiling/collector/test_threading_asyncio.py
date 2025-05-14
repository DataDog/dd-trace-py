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
    import asyncio
    import os
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    # Tests that lock acquire/release events from asyncio threads are captured
    # correctly. See test_asyncio.py for asyncio.Lock tests.
    async def _lock():
        lock = threading.Lock()  # !CREATE! test_lock_acquire_events_1
        lock.acquire()  # !ACQUIRE! test_lock_acquire_events_1
        lock.release()  # !RELEASE! test_lock_acquire_events_1

    def asyncio_run():
        lock = threading.Lock()  # !CREATE! test_lock_acquire_events_2
        lock.acquire()  # !ACQUIRE! test_lock_acquire_events_2
        asyncio.run(_lock())
        lock.release()  # !RELEASE! test_lock_acquire_events_2

    # start a complete profiler so asyncio policy is setup
    p = profiler.Profiler()
    p.start()
    t = threading.Thread(target=asyncio_run, name="foobar")
    t.start()
    t.join()
    p.stop()

    expected_filename = "test_threading_asyncio.py"
    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    linenos_1 = get_lock_linenos("test_lock_acquire_events_1")
    linenos_2 = get_lock_linenos("test_lock_acquire_events_2")

    profile = pprof_utils.parse_profile(output_filename)

    task_name_regex = r"Task-\d+$"

    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="asyncio_run",
                filename=expected_filename,
                linenos=linenos_2,
                lock_name="lock",
                thread_name="foobar",
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="_lock",
                filename=expected_filename,
                linenos=linenos_1,
                lock_name="lock",
                task_name=task_name_regex,
                thread_name="foobar",
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="asyncio_run",
                filename=expected_filename,
                linenos=linenos_2,
                lock_name="lock",
                thread_name="foobar",
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="_lock",
                filename=expected_filename,
                linenos=linenos_1,
                lock_name="lock",
                task_name=task_name_regex,
                thread_name="foobar",
            ),
        ],
    )
