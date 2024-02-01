import os

import pytest

from ddtrace.internal import compat
from ddtrace.profiling.collector import _task


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


def test_get_task_main():
    # type: (...) -> None
    if _task._gevent_tracer is None:
        assert _task.get_task(compat.main_thread.ident) == (None, None, None)


@pytest.mark.subprocess
def test_list_tasks_nogevent():
    from ddtrace.internal import compat
    from ddtrace.profiling.collector import _task

    assert _task.list_tasks(compat.main_thread.ident) == []


@pytest.mark.skipif(not TESTING_GEVENT, reason="only works with gevent")
@pytest.mark.subprocess(ddtrace_run=True)
def test_list_tasks_gevent():
    import gevent.monkey

    gevent.monkey.patch_all()

    import threading

    from ddtrace.profiling.collector import _task

    l1 = threading.Lock()
    l1.acquire()

    def wait():
        l1.acquire()
        l1.release()

    def nothing():
        pass

    t1 = threading.Thread(target=wait, name="t1")
    t1.start()

    tasks = _task.list_tasks(threading.main_thread().ident)
    # can't check == 2 because there are left over from other tests
    assert len(tasks) >= 2

    main_thread_found = False
    t1_found = False
    for task in tasks:
        assert len(task) == 3
        # main thread
        if task[0] == threading.main_thread().ident or task[1] == "MainThread":
            assert task[1] == "MainThread"
            assert task[2] is None
            main_thread_found = True
        # t1
        elif task[0] == t1.ident:
            assert task[1] == "t1"
            assert task[2] is not None
            t1_found = True

    l1.release()

    t1.join()

    assert t1_found
    assert main_thread_found
