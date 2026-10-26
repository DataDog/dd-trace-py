# Each task runs its work in a function with a unique name, so the assertions can pick out that
# task's samples with get_samples_with_function.
import sys

import pytest


@pytest.mark.xfail(
    strict=True, reason="task_a samples get task_b's endpoint: span labels are tracked per thread, not per task"
)
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
    span_ids = {}

    def task_a_work():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    # A enters its span, then B enters its span and sleeps, then A runs. B's span was activated on
    # the thread last, so per-thread labels tag A's samples with B.
    async def task_a(a_active, b_active):
        with tracer.trace("async.request.a", resource=endpoint_a, span_type=ext.SpanTypes.WEB) as span:
            span_ids["a"] = pprof_utils.reinterpret_int_as_int64(span.span_id)
            a_active.set()
            await b_active.wait()
            task_a_work()

    async def task_b_wait():
        await asyncio.sleep(0.6)

    async def task_b(a_active, b_active):
        await a_active.wait()
        with tracer.trace("async.request.b", resource=endpoint_b, span_type=ext.SpanTypes.WEB) as span:
            span_ids["b"] = pprof_utils.reinterpret_int_as_int64(span.span_id)
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

    for samples, endpoint, span_id in (
        (task_a_samples, endpoint_a, span_ids["a"]),
        (task_b_samples, endpoint_b, span_ids["b"]),
    ):
        assert samples
        assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in samples)
        assert all(pprof_utils.get_num_label(profile, sample, "span id") == span_id for sample in samples)
        assert all(pprof_utils.get_num_label(profile, sample, "local root span id") == span_id for sample in samples)


@pytest.mark.xfail(
    strict=True, reason="untraced task samples get the endpoint and span id of a traced task on the thread"
)
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

    # The traced task keeps its span open until the untraced task has finished its work.
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
@pytest.mark.xfail(
    strict=True, reason="task with an explicit empty Context gets the creating thread's endpoint and span"
)
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


@pytest.mark.skipif(
    sys.version_info >= (3, 12),
    reason="Task.get_context() is 3.12+; covers older versions where the task's Context can't be read",
)
@pytest.mark.xfail(strict=True, reason="task created in an empty Context by a custom factory gets the outer endpoint")
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


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.xfail(strict=True, reason="eager task with an explicit empty Context gets the parent task's endpoint")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_eager_empty_task_context",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    # finishes_eagerly: the child completes inside create_task and is never scheduled.
    # suspends: the child also runs a normal step after its eager first step.
    parametrize={"CHILD": ["finishes_eagerly", "suspends"]},
    err=None,
)
def test_stack_respects_explicit_empty_context_for_eager_task():
    """Honor an eager task's explicit empty Context in its eager first step and after it resumes."""
    import asyncio
    import contextvars
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    suspend = os.environ["CHILD"] == "suspends"
    child_ids = []

    def busy_cpu():
        deadline = time.thread_time_ns() + 400_000_000
        while time.thread_time_ns() < deadline:
            pass

    # Separate names so each phase is checked on its own samples.
    def eager_first_step_work():
        busy_cpu()

    def resumed_step_work():
        busy_cpu()

    async def child():
        child_ids.append(id(asyncio.current_task()))
        eager_first_step_work()
        if suspend:
            await asyncio.sleep(0)
            resumed_step_work()

    async def main():
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            with tracer.trace("eager.parent", resource="eager-endpoint", span_type=ext.SpanTypes.WEB):
                await asyncio.create_task(child(), name="eager-child", context=contextvars.Context())
        finally:
            loop.set_task_factory(None)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    asyncio.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    phases = ["eager_first_step_work", "resumed_step_work"] if suspend else ["eager_first_step_work"]
    for function_name in phases:
        samples = [
            sample
            for sample in pprof_utils.get_samples_with_function(profile, wall_samples, function_name)
            if pprof_utils.get_num_label(profile, sample, "task id") == child_ids[0]
        ]
        assert samples, function_name
        assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in samples)
        assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in samples)
        assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in samples)
