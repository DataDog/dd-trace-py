import asyncio
import collections
import sys
import time

from ddtrace.profiling import _asyncio
from ddtrace.profiling import profiler
from ddtrace.profiling.collector.stack import StackCollector


def patch_stack_collector(stack_collector):
    """
    Patch a stack collect so we can count how many times it has run
    """

    def _collect(self):
        self.run_count += 1
        return self._orig_collect()

    stack_collector.run_count = 0
    orig = stack_collector.collect
    stack_collector._orig_collect = orig
    stack_collector.collect = _collect.__get__(stack_collector)


def test_asyncio(tmp_path, monkeypatch) -> None:
    sleep_time = 0.2
    max_wait_for_collector_seconds = 60  # 1 minute timeout

    async def stuff(collector) -> None:
        count = collector.run_count
        start_time = time.time()
        while collector.run_count == count and (time.time() < start_time + max_wait_for_collector_seconds):
            await asyncio.sleep(sleep_time)

    async def hello(collector) -> None:
        t1 = asyncio.create_task(stuff(collector), name="sleep 1")
        t2 = asyncio.create_task(stuff(collector), name="sleep 2")
        await stuff(collector)
        return (t1, t2)

    monkeypatch.setenv("DD_PROFILING_CAPTURE_PCT", "100")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", str(tmp_path / "pprof"))
    # start a complete profiler so asyncio policy is setup
    p = profiler.Profiler()
    stack_collector = [collector for collector in p._profiler._collectors if type(collector) == StackCollector][0]
    patch_stack_collector(stack_collector)

    p.start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    maintask = loop.create_task(hello(stack_collector), name="main")

    # Wait for the collector to run at least once on this thread, while it is doing something
    # 2.5+ seconds at times
    count = stack_collector.run_count
    start_time = time.time()
    while count == stack_collector.run_count and (time.time() < start_time + max_wait_for_collector_seconds):
        pass

    t1, t2 = loop.run_until_complete(maintask)
    events = p._profiler._recorder.reset()
    p.stop()

    wall_time_ns = collections.defaultdict(lambda: 0)

    t1_name = _asyncio._task_get_name(t1)
    t2_name = _asyncio._task_get_name(t2)

    cpu_time_found = False
    main_thread_ran_test = False
    # stack_sample_events = events[stack_event.StackSampleEvent]
    # for event in stack_sample_events:
    #     wall_time_ns[event.task_name] += event.wall_time_ns

    #     first_line_this_test_class = test_asyncio.__code__.co_firstlineno
    #     co_filename, lineno, co_name, class_name = event.frames[0]
    #     if event.task_name == "main":
    #         assert event.thread_name == "MainThread"
    #         assert len(event.frames) == 1
    #         assert co_filename == __file__
    #         assert first_line_this_test_class + 9 <= lineno <= first_line_this_test_class + 15
    #         assert co_name == "hello"
    #         assert class_name == ""
    #         assert event.nframes == 1
    #     elif event.task_name in (t1_name, t2_name):
    #         assert event.thread_name == "MainThread"
    #         assert co_filename == __file__
    #         assert first_line_this_test_class + 4 <= lineno <= first_line_this_test_class + 9
    #         assert co_name == "stuff"
    #         assert class_name == ""
    #         assert event.nframes == 1

    #     if event.thread_name == "MainThread" and event.task_name is None:
    #         # Make sure we account CPU time
    #         if event.cpu_time_ns > 0:
    #             cpu_time_found = True

    #         for frame in event.frames:
    #             if frame[0] == __file__ and frame[2] == "test_asyncio":
    #                 main_thread_ran_test = True

    # assert main_thread_ran_test

    # assert wall_time_ns["main"] > 0, (wall_time_ns, stack_sample_events)

    # assert wall_time_ns[t1_name] > 0
    # assert wall_time_ns[t2_name] > 0
    # if sys.platform != "win32":
    #     # Windows seems to get 0 CPU for this
    #     assert cpu_time_found
