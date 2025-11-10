import threading

from ddtrace.profiling.collector import _task


def test_get_task_main():
    # type: (...) -> None
    assert _task.get_task(threading.main_thread().ident) == (None, None, None)
