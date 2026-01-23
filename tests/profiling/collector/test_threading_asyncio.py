import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_CAPTURE_PCT="100",
        DD_PROFILING_OUTPUT_PPROF="/tmp/asyncio_lock_acquire_events",
        DD_PROFILING_FILE_PATH=__file__,
        DD_PROFILING_API_TIMEOUT_MS="1000",
    ),
    err=None,
)
def test_lock_acquire_events():
    import asyncio
    import faulthandler
    import os
    import threading

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])
    # Dump stack if this subprocess hangs so we can see where it stalls in CI.
    faulthandler.dump_traceback_later(120, repeat=True)

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

    def stop_profiler_with_timeout(timeout_s: float = 5.0) -> None:
        done: list[bool] = []

        def _stop() -> None:
            p._profiler.stop(flush=True, join=False)
            done.append(True)

        stopper = threading.Thread(target=_stop, name="profiler-stop", daemon=True)
        stopper.start()
        stopper.join(timeout_s)
        if not done:
            # Force a traceback dump so CI doesn't hang silently.
            faulthandler.dump_traceback(all_threads=True)
            raise RuntimeError("Profiler stop timed out")

    # start a complete profiler so asyncio policy is setup
    p = profiler.Profiler()
    p.start()
    t = threading.Thread(target=asyncio_run, name="foobar")
    t.start()
    t.join()
    stop_profiler_with_timeout()

    expected_filename = "test_threading_asyncio.py"
    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    linenos_1 = get_lock_linenos("test_lock_acquire_events_1")
    linenos_2 = get_lock_linenos("test_lock_acquire_events_2")

    profile = pprof_utils.parse_newest_profile(output_filename)
    faulthandler.cancel_dump_traceback_later()

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
