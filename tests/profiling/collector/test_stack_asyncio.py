import asyncio
import collections
import os
import sys

import pytest

from ddtrace.profiling import _asyncio
from ddtrace.profiling import profiler
from ddtrace.profiling.collector import stack_event

from . import _asyncio_compat


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if _asyncio_compat.PY38_AND_LATER:
        maintask = loop.create_task(hello(), name="main")
    else:
        maintask = loop.create_task(hello())
    t1, t2 = loop.run_until_complete(maintask)
    events = p._profiler._recorder.reset()
    p.stop()

    wall_time_ns = collections.defaultdict(lambda: 0)

    t1_name = _asyncio._task_get_name(t1)
    t2_name = _asyncio._task_get_name(t2)

    cpu_time_found = False
    stack_sample_events = events[stack_event.StackSampleEvent]
    for event in stack_sample_events:

        wall_time_ns[event.task_name] += event.wall_time_ns

        # This assertion does not work reliably on Python < 3.7
        if _asyncio_compat.PY37_AND_LATER:
            if event.task_name == "main":
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 30, "hello", "")]
                assert event.nframes == 1
            elif event.task_name == t1_name:
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 24, "stuff", "")]
                assert event.nframes == 1
            elif event.task_name == t2_name:
                assert event.thread_name == "MainThread"
                assert event.frames == [(__file__, 24, "stuff", "")]
                assert event.nframes == 1

        if event.thread_name == "MainThread" and (
            # The task name is empty in asyncio (it's not a task) but the main thread is seen as a task in gevent
            (event.task_name is None and not TESTING_GEVENT)
            or (event.task_name == "MainThread" and TESTING_GEVENT)
        ):
            # Make sure we account CPU time
            if event.cpu_time_ns > 0:
                cpu_time_found = True

            for frame in event.frames:
                if frame[0] == __file__ and frame[2] == "test_asyncio":
                    break
            else:
                pytest.fail("unable to find expected main thread frame: %r" % event.frames)

    if _asyncio_compat.PY38_AND_LATER:
        # We don't know the name of this task for Python < 3.8
        assert wall_time_ns["main"] > 0, (wall_time_ns, stack_sample_events)

    assert wall_time_ns[t1_name] > 0
    assert wall_time_ns[t2_name] > 0
    if sys.platform != "win32":
        # Windows seems to get 0 CPU for this
        assert cpu_time_found
