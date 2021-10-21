from ddtrace import compat
from ddtrace.internal import nogevent
from ddtrace.profiling.collector import _task


def test_get_task_main():
    # type: (...) -> None
    if _task._gevent_tracer is None:
        assert _task.get_task(nogevent.main_thread_id) == (None, None, None)
    else:
        assert _task.get_task(nogevent.main_thread_id) == (compat.main_thread.ident, "MainThread", None)
