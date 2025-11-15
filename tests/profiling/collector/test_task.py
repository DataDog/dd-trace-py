import os
import threading

from ddtrace.profiling.collector import _task


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


def test_get_task_main():
    # type: (...) -> None
    assert _task.get_task(threading.main_thread().ident) == (None, None, None)
