import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_wall_time_on_and_off_cpu",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_wall_time_on_and_off_cpu() -> None:
    import asyncio
    import math
    import os
    import time
    import uuid

    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    def factorial(result: int, n: int) -> int:
        result *= math.factorial(n)
        return result

    async def cpu_bound_work(duration: float) -> None:
        start = time.time()
        end_time = start + duration
        result = 1
        while time.time() < end_time:
            factorial(result, 1000)

    async def io_simulation(duration: float) -> None:
        await asyncio.sleep(duration)

    async def mixed_workload(cpu_duration: float, io_duration: float) -> None:
        await cpu_bound_work(cpu_duration)
        await io_simulation(io_duration)

    async def main() -> None:
        execution_time_sec = 2

        tasks = [
            asyncio.create_task(cpu_bound_work(execution_time_sec), name="cpu_bound_work"),
            asyncio.create_task(
                mixed_workload(execution_time_sec * 0.5, execution_time_sec * 0.5), name="mixed_workload"
            ),
            asyncio.create_task(io_simulation(execution_time_sec), name="io_simulation"),
        ]

        await asyncio.gather(*tasks)
        await cpu_bound_work(execution_time_sec * 0.3)

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("test_asyncio", resource=resource, span_type=span_type) as span:
        local_root_span_id = span._local_root.span_id

        asyncio.run(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    def loc(f_name: str, file: str = "", line_no: int = -1) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=f_name, filename=file, line_no=line_no)

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="cpu_bound_work",
            local_root_span_id=local_root_span_id,
            locations=[
                loc("factorial", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("cpu_bound_work", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("main", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("run", "runners.py"),
                loc("<module>", "test_asyncio_wall_time_on_and_off_cpu.py"),
            ],
        ),
        print_samples_on_failure=True,
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="io_simulation",
            local_root_span_id=local_root_span_id,
            locations=[
                loc("sleep", "tasks.py"),
                loc("io_simulation", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("main", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("run", "runners.py"),
                loc("<module>", "test_asyncio_wall_time_on_and_off_cpu.py"),
            ],
        ),
        print_samples_on_failure=True,
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="mixed_workload",
            local_root_span_id=local_root_span_id,
            locations=[
                loc("factorial", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("cpu_bound_work", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("mixed_workload", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("main", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("run", "runners.py"),
                loc("<module>", "test_asyncio_wall_time_on_and_off_cpu.py"),
            ],
        ),
        print_samples_on_failure=True,
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="mixed_workload",
            local_root_span_id=local_root_span_id,
            locations=[
                loc("sleep", "tasks.py"),
                loc("io_simulation", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("mixed_workload", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("main", "test_asyncio_wall_time_on_and_off_cpu.py"),
                loc("run", "runners.py"),
                loc("<module>", "test_asyncio_wall_time_on_and_off_cpu.py"),
            ],
        ),
        print_samples_on_failure=True,
    )
