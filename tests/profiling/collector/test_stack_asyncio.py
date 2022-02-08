import asyncio
import collections

import pytest

from ddtrace.profiling import _asyncio
from ddtrace.profiling import profiler
from ddtrace.profiling.collector import stack_event

from . import _asyncio_compat


@pytest.mark.skipif(not _asyncio_compat.PY36_AND_LATER, reason="Python > 3.5 needed")
def test_asyncio(tmp_path, monkeypatch) -> None:
    sleep_time = 0.2
    sleep_times = 5

    async def stuff() -> None:
        await asyncio.sleep(sleep_time)

    async def hello() -> None:
        t1 = _asyncio_compat.create_task(stuff(), name="sleep 1")
        t2 = _asyncio_compat.create_task(stuff(), name="sleep 2")
        for _ in range(sleep_times):
            await stuff()
        return (t1, t2)

    monkeypatch.setenv("DD_PROFILING_CAPTURE_PCT", "100")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", str(tmp_path / "pprof"))
    # start a complete profiler so asyncio policy is setup
    p = profiler.Profiler()
    p.start()
    t1, t2 = _asyncio_compat.run(hello())
    events = p._profiler._recorder.reset()
    p.stop()

    wall_time_ns = collections.defaultdict(lambda: 0)

    t1_name = _asyncio._task_get_name(t1)
    t2_name = _asyncio._task_get_name(t2)

    for event in events[stack_event.StackSampleEvent]:

        wall_time_ns[event.task_name] += event.wall_time_ns

        # This assertion does not work reliably on Python < 3.7
        if _asyncio_compat.PY37_AND_LATER:
            if event.task_name == "Task-1":
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 25, "hello")]
                assert event.nframes == 1
            elif event.task_name == t1_name:
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 19, "stuff")]
                assert event.nframes == 1
            elif event.task_name == t2_name:
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 19, "stuff")]
                assert event.nframes == 1

    if _asyncio_compat.PY38_AND_LATER:
        # We don't know the name of this task for Python < 3.8
        assert wall_time_ns["Task-1"] > 0

    assert wall_time_ns[t1_name] > 0
    assert wall_time_ns[t2_name] > 0
