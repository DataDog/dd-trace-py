"""A loop thread's CPU time goes to one sample per sampling tick: the running task's, or a thread sample."""

from collections import Counter
from dataclasses import dataclass
from dataclasses import field
import sys
import time

import pytest


def burn(ground_truth: "Counter[str]", name: str, seconds: float) -> None:
    """Spin for seconds of thread CPU time and add the CPU time it used to ground_truth[name], in nanoseconds."""
    start = time.thread_time_ns()
    deadline = start + int(seconds * 1e9)
    while time.thread_time_ns() < deadline:
        pass
    ground_truth[name] += time.thread_time_ns() - start


@dataclass
class CpuTime:
    """CPU time of the MainThread samples of a profile, in nanoseconds."""

    total: int = 0
    # Thread samples, which have no task.
    thread: int = 0
    per_task: "Counter[str]" = field(default_factory=Counter)
    # Task samples that are not in burn. Only a task that is running when the sampler reads the thread can be in burn.
    task_outside_burn: int = 0


def cpu_time(profile) -> CpuTime:
    from tests.profiling.collector import pprof_utils

    cpu_index = pprof_utils.get_sample_type_index(profile, "cpu-time")
    result = CpuTime()
    for sample in profile.sample:
        cpu = sample.value[cpu_index]
        if cpu == 0 or pprof_utils.get_str_label(profile, sample, "thread name") != "MainThread":
            continue
        result.total += cpu
        task_name = pprof_utils.get_str_label(profile, sample, "task name")
        if task_name is None:
            result.thread += cpu
            continue
        result.per_task[task_name] += cpu
        functions = {
            pprof_utils.get_location_from_id(profile, location_id).function_name for location_id in sample.location_id
        }
        if "burn" not in functions:
            result.task_outside_burn += cpu
    return result


def assert_cpu_time_matches(profile_cpu: CpuTime, ground_truth: "Counter[str]", thread_cpu_ns: int) -> None:
    """Check per-task CPU time against ground_truth, and that the thread's CPU time is charged once, to no idle task."""
    # The sampler charges the CPU time used since the previous tick, about 10 ms, to the task running at the tick.
    # A burst therefore loses up to one tick of CPU time to whatever runs next, and gains up to one from whatever ran
    # before. The bursts in these tests are long enough for that to stay within 20%.
    for name, expected in ground_truth.items():
        actual = profile_cpu.per_task[name]
        assert abs(actual - expected) <= 0.2 * expected + 30e6, (name, actual / 1e9, expected / 1e9, profile_cpu)
    # A task that is not running gets no CPU time. A task sampled while it runs outside burn, such as just before
    # or after it, still gets that tick's CPU time, about 10 ms. Allow two such ticks.
    assert profile_cpu.task_outside_burn <= 0.01 * profile_cpu.total + 20e6, profile_cpu
    # Charged once, and not lost when no task runs.
    assert 0.8 * thread_cpu_ns <= profile_cpu.total <= 1.05 * thread_cpu_ns, (profile_cpu, thread_cpu_ns / 1e9)


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_cpu_time_gather",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_cpu_time_gather() -> None:
    import asyncio
    from collections import Counter
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_asyncio_cpu_time import assert_cpu_time_matches
    from tests.profiling.collector.test_asyncio_cpu_time import burn
    from tests.profiling.collector.test_asyncio_cpu_time import cpu_time
    from tests.profiling.collector.test_utils import async_run

    ground_truth: Counter[str] = Counter()

    async def worker(name: str, burn_s: float, sleep_s: float, end: float) -> None:
        while time.monotonic() < end:
            if burn_s:
                burn(ground_truth, name, burn_s)
            await asyncio.sleep(sleep_s)

    async def main() -> None:
        end = time.monotonic() + 4
        workers = [("alpha", 0.05, 0.25), ("beta", 0.1, 0.3), ("gamma", 0.15, 0.35), ("sleeper", 0, 4)]
        await asyncio.gather(*(asyncio.create_task(worker(n, b, s, end), name=n) for n, b, s in workers))

    thread_cpu_start = time.thread_time_ns()
    p = profiler.Profiler()
    p.start()
    async_run(main())
    p.stop()
    thread_cpu_ns = time.thread_time_ns() - thread_cpu_start

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    ground_truth["sleeper"] = 0
    assert_cpu_time_matches(cpu_time(profile), ground_truth, thread_cpu_ns)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio.TaskGroup requires Python 3.11+")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_cpu_time_taskgroup",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_cpu_time_taskgroup() -> None:
    import asyncio
    from collections import Counter
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_asyncio_cpu_time import assert_cpu_time_matches
    from tests.profiling.collector.test_asyncio_cpu_time import burn
    from tests.profiling.collector.test_asyncio_cpu_time import cpu_time
    from tests.profiling.collector.test_utils import async_run

    ground_truth: Counter[str] = Counter()

    async def worker(name: str, burn_s: float, sleep_s: float, end: float) -> None:
        while time.monotonic() < end:
            burn(ground_truth, name, burn_s)
            await asyncio.sleep(sleep_s)

    async def sleeper(end: float) -> None:
        await asyncio.sleep(end - time.monotonic())

    async def gatherer(end: float) -> None:
        await asyncio.gather(
            asyncio.create_task(worker("grandchild", 0.05, 0.25, end), name="grandchild"),
            asyncio.create_task(sleeper(end), name="sleeper"),
        )

    async def main() -> None:
        end = time.monotonic() + 4
        async with asyncio.TaskGroup() as tg:  # type: ignore[attr-defined]
            tg.create_task(worker("child", 0.1, 0.3, end), name="child")
            tg.create_task(gatherer(end), name="gatherer")

    thread_cpu_start = time.thread_time_ns()
    p = profiler.Profiler()
    p.start()
    async_run(main())
    p.stop()
    thread_cpu_ns = time.thread_time_ns() - thread_cpu_start

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    ground_truth["sleeper"] = 0
    assert_cpu_time_matches(cpu_time(profile), ground_truth, thread_cpu_ns)


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_cpu_time_idle_loop",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_cpu_time_idle_loop_goes_to_no_task() -> None:
    """CPU time used while no task runs, here in plain loop callbacks, goes to the thread, not to a pending task."""
    import asyncio
    from collections import Counter
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_asyncio_cpu_time import burn
    from tests.profiling.collector.test_asyncio_cpu_time import cpu_time
    from tests.profiling.collector.test_utils import async_run

    ground_truth: Counter[str] = Counter()

    def callback(loop: asyncio.AbstractEventLoop, end: float) -> None:
        burn(ground_truth, "callback", 0.02)
        if time.monotonic() < end:
            loop.call_later(0.03, callback, loop, end)

    async def sleeper(seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.call_soon(callback, loop, time.monotonic() + 2)
        await asyncio.gather(*(asyncio.create_task(sleeper(2.5), name="sleeper%d" % i) for i in range(3)))

    p = profiler.Profiler()
    p.start()
    async_run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    profile_cpu = cpu_time(profile)
    # No task ever runs burn, so the CPU time of task samples is only task steps' own overhead.
    assert profile_cpu.task_outside_burn <= 0.05 * ground_truth["callback"], (profile_cpu, ground_truth)
    # The callback's CPU time goes to thread samples. Some of it is charged at a tick after the callback returned, so
    # those samples have the idle loop's stack instead of the callback's.
    assert profile_cpu.thread >= 0.9 * ground_truth["callback"], (profile_cpu, ground_truth)
    callback_samples = pprof_utils.get_samples_with_function(
        profile, pprof_utils.get_samples_with_value_type(profile, "cpu-time"), "callback"
    )
    assert callback_samples
    assert all(pprof_utils.get_str_label(profile, sample, "task name") is None for sample in callback_samples)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_cpu_time_eager_step",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
        # Timestamps let the test group samples by sampling tick.
        DD_PROFILING_TIMELINE_ENABLED="1",
    ),
    err=None,
)
def test_asyncio_cpu_time_eager_step_counted_once() -> None:
    """During an eager step, the creator and the eager task both look running. The thread's CPU time is charged once.

    On Python 3.14+, CPython records the eager task as running, so it owns the tick's only sample.
    """
    import asyncio
    from collections import Counter
    import os
    import sys
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_asyncio_cpu_time import burn

    ground_truth: Counter[str] = Counter()
    eager_task_ids = set()

    async def eager_child() -> None:
        eager_task_ids.add(id(asyncio.current_task()))
        burn(ground_truth, "eager_child", 0.05)

    async def creator() -> None:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        try:
            end = time.monotonic() + 2
            while time.monotonic() < end:
                await asyncio.create_task(eager_child())
                await asyncio.sleep(0.01)
        finally:
            loop.set_task_factory(None)

    thread_cpu_start = time.thread_time_ns()
    p = profiler.Profiler()
    p.start()
    asyncio.run(creator())
    p.stop()
    thread_cpu_ns = time.thread_time_ns() - thread_cpu_start

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_index = pprof_utils.get_sample_type_index(profile, "cpu-time")
    main_samples = [
        sample for sample in profile.sample if pprof_utils.get_str_label(profile, sample, "thread name") == "MainThread"
    ]
    profile_cpu_ns = sum(sample.value[cpu_index] for sample in main_samples)
    # Without the fix, the creator and the eager task were both charged the eager step's CPU time.
    assert 0.8 * thread_cpu_ns <= profile_cpu_ns <= 1.05 * thread_cpu_ns, (profile_cpu_ns / 1e9, thread_cpu_ns / 1e9)

    if sys.version_info >= (3, 14):
        step_samples = pprof_utils.get_samples_with_function(profile, main_samples, "burn")
        assert step_samples
        samples_per_tick = Counter(
            pprof_utils.get_num_label(profile, sample, "end_timestamp_ns") for sample in step_samples
        )
        assert set(samples_per_tick.values()) == {1}, {tick: n for tick, n in samples_per_tick.items() if n > 1}
        owners = Counter(
            pprof_utils.get_num_label(profile, sample, "task id") in eager_task_ids for sample in step_samples
        )
        # The sampler reads the thread while it runs, so a tick at the end of a step can see its task as finished.
        assert owners[False] <= 0.05 * len(step_samples), owners
        step_cpu_ns = sum(sample.value[cpu_index] for sample in step_samples)
        assert step_cpu_ns <= 1.05 * ground_truth["eager_child"], (step_cpu_ns / 1e9, ground_truth)
