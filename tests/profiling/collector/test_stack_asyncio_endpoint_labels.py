import sys

import pytest

from tests.profiling.collector import test_utils


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_asyncio_task_endpoints",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_keeps_asyncio_task_endpoints_separate():
    """Keep concurrent tasks associated with their own trace endpoints."""
    import asyncio
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint_a = "async-endpoint-a"
    endpoint_b = "async-endpoint-b"

    def task_a_work():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def task_a(a_active, b_active):
        with tracer.trace("async.request.a", resource=endpoint_a, span_type=ext.SpanTypes.WEB):
            a_active.set()
            await b_active.wait()
            task_a_work()

    async def task_b_wait():
        await asyncio.sleep(0.6)

    async def task_b(a_active, b_active):
        await a_active.wait()
        with tracer.trace("async.request.b", resource=endpoint_b, span_type=ext.SpanTypes.WEB):
            b_active.set()
            await task_b_wait()

    async def main():
        a_active = asyncio.Event()
        b_active = asyncio.Event()
        await asyncio.gather(
            asyncio.create_task(task_a(a_active, b_active), name="endpoint-task-a"),
            asyncio.create_task(task_b(a_active, b_active), name="endpoint-task-b"),
        )

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    asyncio.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    task_a_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "task_a_work")
    task_b_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "task_b_wait")

    assert task_a_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint_a for sample in task_a_samples)
    assert task_b_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint_b for sample in task_b_samples)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_untraced_asyncio_task",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_does_not_use_thread_endpoint_for_untraced_asyncio_task():
    """Never borrow a traced task's endpoint for an untraced task on the same thread."""
    import asyncio
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "traced-task-endpoint"

    async def traced_task(traced_task_active, untraced_task_finished):
        with tracer.trace("traced.task.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
            traced_task_active.set()
            await untraced_task_finished.wait()

    def untraced_task_work():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def untraced_task(traced_task_active, untraced_task_finished):
        await traced_task_active.wait()
        untraced_task_work()
        untraced_task_finished.set()

    async def main():
        traced_task_active = asyncio.Event()
        untraced_task_finished = asyncio.Event()
        await asyncio.gather(
            asyncio.create_task(traced_task(traced_task_active, untraced_task_finished), name="traced-endpoint-task"),
            asyncio.create_task(
                untraced_task(traced_task_active, untraced_task_finished), name="untraced-endpoint-task"
            ),
        )

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    asyncio.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    untraced_task_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "untraced_task_work")

    assert untraced_task_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in untraced_task_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in untraced_task_samples)
    assert all(
        pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in untraced_task_samples
    )


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio task context requires Python 3.11+")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_explicit_empty_task_context",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_respects_explicit_empty_child_task_context():
    """Honor an explicit empty child Context instead of inheriting the creator's endpoint."""
    import asyncio
    import contextvars
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "outer-thread-endpoint"

    def explicit_empty_context_work():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def explicit_context_child():
        explicit_empty_context_work()

    async def main():
        await asyncio.create_task(
            explicit_context_child(), name="explicit-empty-context-child", context=contextvars.Context()
        )

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("outer.thread.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
        asyncio.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")

    child_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "explicit_empty_context_work")
    assert child_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in child_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in child_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in child_samples)


@pytest.mark.skipif(not test_utils.uvloop_available(), reason="uvloop is not installed in this environment")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_uvloop_raw_gather_context",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_publishes_inherited_span_to_uvloop_raw_gather_task():
    """Seed uvloop tasks created by gather from their inherited span context."""
    import asyncio
    import os
    import time

    import uvloop

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "uvloop-gather-endpoint"

    def uvloop_raw_gather_work():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def cpu_child():
        uvloop_raw_gather_work()

    async def sleeping_child():
        await asyncio.sleep(0.6)

    async def parent_task():
        with tracer.trace("uvloop.parent.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
            await asyncio.gather(cpu_child(), sleeping_child())

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    uvloop.run(parent_task())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    child_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "uvloop_raw_gather_work")

    assert child_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in child_samples)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_unregistered_asyncio_loop",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_tracks_asyncio_loop_without_set_event_loop():
    """Register loops started directly through run_until_complete for task attribution."""
    import asyncio
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "unregistered-loop-endpoint"

    def unregistered_loop_task_work():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def main():
        with tracer.trace("unregistered.loop.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
            unregistered_loop_task_work()

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    samples = pprof_utils.get_samples_with_function(profile, wall_samples, "unregistered_loop_task_work")

    assert samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in samples)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio task context requires Python 3.11+")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_rejects_finished_copied_task_context",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_rejects_finished_span_from_copied_task_context():
    """Reject copied task context after its source span has finished."""
    import asyncio
    import contextvars
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    def work_from_finished_context():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def child():
        work_from_finished_context()

    async def main(copied_context):
        await asyncio.create_task(child(), name="finished-copied-context", context=copied_context)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("finished.copied.request", resource="finished-copied-endpoint", span_type=ext.SpanTypes.WEB):
        copied_context = contextvars.copy_context()
    asyncio.run(main(copied_context))
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    samples = pprof_utils.get_samples_with_function(profile, wall_samples, "work_from_finished_context")

    assert samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in samples)


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="covers task factories without Task.get_context()")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_pre312_custom_task_context",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_omits_unverifiable_pre312_custom_factory_context():
    """Omit pre-3.12 attribution when a custom factory's actual Context cannot be inspected."""
    import asyncio
    import contextvars
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "pre312-outer-endpoint"

    def custom_factory_work():
        deadline = time.thread_time_ns() + 400_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def child():
        custom_factory_work()

    async def main():
        loop = asyncio.get_running_loop()

        def empty_context_task_factory(loop, coro):
            return contextvars.Context().run(asyncio.Task, coro, loop=loop)

        loop.set_task_factory(empty_context_task_factory)
        try:
            await asyncio.create_task(child())
        finally:
            loop.set_task_factory(None)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("outer.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
        asyncio.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    samples = pprof_utils.get_samples_with_function(profile, wall_samples, "custom_factory_work")

    assert samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in samples)
