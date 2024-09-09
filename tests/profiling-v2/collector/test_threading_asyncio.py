import os
import threading

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import threading as collector_threading
from tests.profiling.collector import _asyncio_compat
from tests.profiling.collector import pprof_utils
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos


init_linenos(__file__)


def test_lock_acquire_events():
    # Tests that lock acquire/release events from asyncio threads are captured
    # correctly. See test_asyncio.py for asyncio.Lock tests.
    async def _lock():
        lock = threading.Lock()  # !CREATE! test_lock_acquire_events_1
        lock.acquire()  # !ACQUIRE! test_lock_acquire_events_1
        lock.release()  # !RELEASE! test_lock_acquire_events_1

    def asyncio_run():
        lock = threading.Lock()  # !CREATE! test_lock_acquire_events_2
        lock.acquire()  # !ACQUIRE! test_lock_acquire_events_2
        _asyncio_compat.run(_lock())
        lock.release()  # !RELEASE! test_lock_acquire_events_2

    output_prefix = "/tmp/asyncio_lock_acquire_events"

    ddup.config(
        env="test",
        service="test_lock_acquire_events",
        version="my_version",
        output_filename=output_prefix,
    )
    ddup.start()

    with collector_threading.ThreadingLockCollector(None, capture_pct=100):
        t = threading.Thread(target=asyncio_run, name="foobar")
        t.start()
        t.join()

    ddup.upload()

    linenos_1 = get_lock_linenos("test_lock_acquire_events_1")
    linenos_2 = get_lock_linenos("test_lock_acquire_events_2")

    profile = pprof_utils.parse_profile(output_prefix + "." + str(os.getpid()))

    task_name_regex = r"Task-\d+$"

    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="asyncio_run",
                filename=os.path.basename(__file__),
                linenos=linenos_2,
                lock_name="lock",
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="_lock",
                filename=os.path.basename(__file__),
                linenos=linenos_1,
                lock_name="lock",
                task_name=task_name_regex,
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="asyncio_run",
                filename=os.path.basename(__file__),
                linenos=linenos_2,
                lock_name="lock",
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="_lock",
                filename=os.path.basename(__file__),
                linenos=linenos_1,
                lock_name="lock",
                task_name=task_name_regex,
            ),
        ],
    )
