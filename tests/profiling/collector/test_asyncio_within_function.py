import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_within_function",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_within_function():
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

    def synchronous_code_dep() -> None:
        time.sleep(0.25)

    def synchronous_code() -> None:
        synchronous_code_dep()

    async def inner() -> None:
        synchronous_code()

    async def outer() -> None:
        await inner()
        await asyncio.sleep(0.25)

    async def async_main() -> None:
        await outer()

    def async_starter() -> None:
        asyncio.run(async_main())

    def sync_main() -> None:
        async_starter()
        time.sleep(0.25)

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("test_asyncio", resource=resource, span_type=span_type) as span:
        span_id = span.span_id
        local_root_span_id = span._local_root.span_id

        sync_main()

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
                        loc("sync_main"),
                        loc("async_starter"),
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
                        loc("inner"),
                        loc("synchronous_code"),
                        loc("synchronous_code_dep"),
                        # We don't have time.sleep because it's a C function.
                    ]
                )
            ),
        ),
        print_samples_on_failure=True,
    )
