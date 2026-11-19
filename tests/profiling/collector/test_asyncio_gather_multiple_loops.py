import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_gather_multiple_loops",
        _DD_PROFILING_STACK_MAX_THREADS="1",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    err=None,
)
def test_asyncio_gather_multiple_loops() -> None:
    import asyncio
    import os
    import threading

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg
    ready = threading.Barrier(2)

    async def child() -> None:
        await asyncio.sleep(2)

    async def parent() -> None:
        task = asyncio.create_task(child(), name="gather-child")
        await asyncio.gather(task)

    def run_loop() -> None:
        ready.wait(timeout=10)
        async_run(parent())

    p = profiler.Profiler()
    p.start()
    threads = [threading.Thread(target=run_loop, name=f"loop-{i}") for i in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    finally:
        p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    st = profile.string_table
    for thread in threads:
        child_samples = [
            sample
            for sample in samples
            if pprof_utils.get_str_label(profile, sample, "thread name") == thread.name
            and pprof_utils.get_str_label(profile, sample, "task name") == "gather-child"
        ]
        assert child_samples, f"No child samples from {thread.name}"
        # Check the latest sample, not just an early sample taken before another loop could prune the link.
        latest = max(
            child_samples,
            key=lambda sample: pprof_utils.get_label_with_key(st, sample, "end_timestamp_ns").num,
        )
        pprof_utils.assert_profile_has_sample(
            profile,
            [latest],
            expected_sample=pprof_utils.StackEvent(
                thread_name=thread.name,
                task_name="gather-child",
                locations=[
                    pprof_utils.StackLocation(function_name="sleep", filename="", line_no=-1),
                    pprof_utils.StackLocation(function_name="child", filename="", line_no=-1),
                    pprof_utils.StackLocation(function_name="parent", filename="", line_no=-1),
                ],
            ),
            print_samples_on_failure=True,
        )
