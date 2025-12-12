import pytest


@pytest.mark.xfail(reason="This test is flaky due to a race condition, see PROF-13137")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_recursive_on_cpu_tasks",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_recursive_on_cpu_tasks():
    import asyncio
    import os
    from sys import version_info as PYVERSION
    import time
    import uuid

    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.pprof_utils import StackLocation

    assert stack.is_available, stack.failure_msg

    def sync_code() -> int:
        target = time.time() + 1
        result = 0
        while time.time() < target:
            result += 1

        return result

    def sync_code_outer() -> int:
        return sync_code()

    async def inner3() -> int:
        return sync_code_outer()

    async def inner2() -> int:
        return await inner3()

    async def inner1() -> int:
        t = asyncio.create_task(inner2())

        return await t

    async def outer():
        return await inner1()

    async def async_main():
        return await outer()

    def main_sync():
        asyncio.run(async_main())

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("test_asyncio", resource=resource, span_type=span_type) as span:
        span_id = span.span_id
        local_root_span_id = span._local_root.span_id

        main_sync()

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    def loc(f_name: str) -> StackLocation:
        return pprof_utils.StackLocation(function_name=f_name, filename="", line_no=-1)

    runner_prefix = "Runner." if PYVERSION >= (3, 11) else ""
    base_event_loop_prefix = "BaseEventLoop." if PYVERSION >= (3, 11) else ""
    handle_prefix = "Handle." if PYVERSION >= (3, 11) else ""

    pprof_utils.assert_profile_has_sample(
        profile,
        list(profile.sample),
        pprof_utils.StackEvent(
            thread_name="MainThread",
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            locations=list(
                reversed(
                    [
                        loc("<module>"),
                        loc("main_sync"),
                        loc("run"),
                    ]
                    + ([loc(f"{runner_prefix}run")] if PYVERSION >= (3, 11) else [])
                    + [
                        loc(f"{base_event_loop_prefix}run_until_complete"),
                        loc(f"{base_event_loop_prefix}run_forever"),
                        loc(f"{base_event_loop_prefix}_run_once"),
                        loc(f"{handle_prefix}_run"),
                        # loc("Task-1"),
                        loc("async_main"),
                        loc("outer"),
                        loc("inner1"),
                        # loc("Task-2"),
                        loc("inner2"),
                        loc("inner3"),
                        loc("sync_code_outer"),
                        loc("sync_code"),
                    ]
                )
            ),
        ),
        print_samples_on_failure=True,
    )

    # Same test, but with a specific task_name ("Task-1")``
    # Ideally, we should be able to report the Task-2 specific part in its own
    # Stack, but at the moment we are not. This will be fixed in the future.
    pprof_utils.assert_profile_has_sample(
        profile,
        list(profile.sample),
        pprof_utils.StackEvent(
            thread_name="MainThread",
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            task_name="Task-1",
            locations=list(
                reversed(
                    [
                        loc("<module>"),
                        loc("main_sync"),
                        loc("run"),
                        loc("Runner.run"),
                        loc("BaseEventLoop.run_until_complete"),
                        loc("BaseEventLoop.run_forever"),
                        loc("BaseEventLoop._run_once"),
                        loc("Handle._run"),
                        # loc("Task-1"),
                        loc("async_main"),
                        loc("outer"),
                        loc("inner1"),
                        # loc("Task-2"),
                        loc("inner2"),
                        loc("inner3"),
                        loc("sync_code_outer"),
                        loc("sync_code"),
                    ]
                )
            ),
        ),
        print_samples_on_failure=True,
    )
