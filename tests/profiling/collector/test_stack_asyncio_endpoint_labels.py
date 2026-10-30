import sys

import pytest

from tests.profiling.collector.test_stack_asyncio_task_attribution import SUBPROCESS_TIMEOUT_SECONDS


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_asyncio_task_endpoints",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    parametrize={"ASYNCIO_INTEGRATION": ["off", "on"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_stack_keeps_asyncio_task_endpoints_separate() -> None:
    """Keep concurrent tasks associated with their own trace endpoints."""
    import asyncio
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    patch_asyncio_integration()
    endpoint_a = "async-endpoint-a"
    endpoint_b = "async-endpoint-b"
    span_ids: dict[str, int] = {}
    task_ids: dict[str, int] = {}

    def task_a_work() -> None:
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    # A enters its span, then B enters its span and sleeps, then A runs. B's span was activated on
    # the thread last, so per-thread labels tag A's samples with B.
    async def task_a(a_active: asyncio.Event, b_active: asyncio.Event) -> None:
        with tracer.trace("async.request.a", resource=endpoint_a, span_type=ext.SpanTypes.WEB) as span:
            span_ids["a"] = pprof_utils.reinterpret_int_as_int64(span.span_id)
            task_ids["a"] = id(asyncio.current_task())
            a_active.set()
            await b_active.wait()
            wait_for_in_flight_samples()
            task_a_work()
            wait_for_in_flight_samples()

    async def task_b_wait() -> None:
        await asyncio.sleep(0.6)

    async def task_b(a_active: asyncio.Event, b_active: asyncio.Event) -> None:
        await a_active.wait()
        with tracer.trace("async.request.b", resource=endpoint_b, span_type=ext.SpanTypes.WEB) as span:
            span_ids["b"] = pprof_utils.reinterpret_int_as_int64(span.span_id)
            task_ids["b"] = id(asyncio.current_task())
            # This activation creates B's key. Wait, so no sample that started before shows task_b_wait.
            wait_for_in_flight_samples()
            b_active.set()
            await task_b_wait()
            wait_for_in_flight_samples()

    async def main() -> None:
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
    # The isolation check: every sample of a task carries that task's span, or no span before the span starts and
    # after it finishes, and never the other task's span or the thread's span.
    for task, endpoint, span_id in (("a", endpoint_a, span_ids["a"]), ("b", endpoint_b, span_ids["b"])):
        task_samples = [s for s in wall_samples if pprof_utils.get_num_label(profile, s, "task id") == task_ids[task]]
        assert task_samples
        assert {pprof_utils.get_str_label(profile, s, "trace endpoint") for s in task_samples} <= {endpoint, None}
        assert {pprof_utils.get_num_label(profile, s, "span id") for s in task_samples} <= {span_id, None}
        assert {pprof_utils.get_num_label(profile, s, "local root span id") for s in task_samples} <= {span_id, None}

    task_a_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "task_a_work")
    # B is checked while it is suspended in task_b_wait. Its frames are on the thread only as it starts and stops
    # waiting, and a sample taken then can show them under another task: Echion can pair the thread's frames with the
    # wrong task (the TODO in unwind_tasks, where the on-CPU task may not match the thread stack). The task id filter
    # only excludes that frame duplication. The check above is the isolation check.
    task_b_samples = [
        s
        for s in pprof_utils.get_samples_with_function(profile, wall_samples, "task_b_wait")
        if pprof_utils.get_num_label(profile, s, "task id") == task_ids["b"]
    ]

    for samples, endpoint, span_id in (
        (task_a_samples, endpoint_a, span_ids["a"]),
        (task_b_samples, endpoint_b, span_ids["b"]),
    ):
        assert samples
        assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in samples)
        assert all(pprof_utils.get_num_label(profile, sample, "span id") == span_id for sample in samples)
        assert all(pprof_utils.get_num_label(profile, sample, "local root span id") == span_id for sample in samples)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_untraced_asyncio_task",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    parametrize={"ASYNCIO_INTEGRATION": ["off", "on"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_stack_does_not_use_thread_endpoint_for_untraced_asyncio_task() -> None:
    """Never borrow a traced task's endpoint for an untraced task on the same thread."""
    import asyncio
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    patch_asyncio_integration()
    endpoint = "traced-task-endpoint"

    # The traced task keeps its span open until the untraced task has finished its work.
    async def traced_task(traced_task_active: asyncio.Event, untraced_task_finished: asyncio.Event) -> None:
        with tracer.trace("traced.task.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
            traced_task_active.set()
            await untraced_task_finished.wait()

    def untraced_task_work() -> None:
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def untraced_task(traced_task_active: asyncio.Event, untraced_task_finished: asyncio.Event) -> None:
        await traced_task_active.wait()
        wait_for_in_flight_samples()
        untraced_task_work()
        wait_for_in_flight_samples()
        untraced_task_finished.set()

    async def main() -> None:
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
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_stack_respects_explicit_empty_child_task_context() -> None:
    """Honor an explicit empty child Context instead of inheriting the creator's endpoint."""
    import asyncio
    import contextvars
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    endpoint = "outer-thread-endpoint"

    def explicit_empty_context_work() -> None:
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def explicit_context_child() -> None:
        wait_for_in_flight_samples()
        explicit_empty_context_work()
        wait_for_in_flight_samples()

    async def main() -> None:
        await asyncio.create_task(
            explicit_context_child(),
            name="explicit-empty-context-child",
            context=contextvars.Context(),  # type: ignore[call-arg]
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
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_pre312_custom_task_context",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_stack_omits_unverifiable_pre312_custom_factory_context() -> None:
    """Omit pre-3.12 attribution when a custom factory's actual Context cannot be inspected."""
    import asyncio
    import contextvars
    import os
    import time
    from typing import Any

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    endpoint = "pre312-outer-endpoint"

    def custom_factory_work() -> None:
        deadline = time.thread_time_ns() + 400_000_000
        while time.thread_time_ns() < deadline:
            pass

    async def child() -> None:
        wait_for_in_flight_samples()
        custom_factory_work()
        wait_for_in_flight_samples()

    async def main() -> None:
        loop = asyncio.get_running_loop()

        def empty_context_task_factory(loop: asyncio.AbstractEventLoop, coro: Any) -> asyncio.Task[Any]:
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
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_eager_empty_task_context",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    # finishes_eagerly: the child completes inside create_task and is never scheduled.
    # suspends: the child also runs a normal step after its eager first step.
    parametrize={"CHILD": ["finishes_eagerly", "suspends"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_stack_respects_explicit_empty_context_for_eager_task() -> None:
    """Honor an eager task's explicit empty Context in its eager first step and after it resumes."""
    import asyncio
    import contextvars
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    suspend = os.environ["CHILD"] == "suspends"
    child_ids: list[int] = []

    def busy_cpu() -> None:
        deadline = time.thread_time_ns() + 400_000_000
        while time.thread_time_ns() < deadline:
            pass

    # Separate names so each phase is checked on its own samples.
    def eager_first_step_work() -> None:
        busy_cpu()

    def resumed_step_work() -> None:
        busy_cpu()

    async def child() -> None:
        child_ids.append(id(asyncio.current_task()))
        wait_for_in_flight_samples()
        eager_first_step_work()
        wait_for_in_flight_samples()
        if suspend:
            await asyncio.sleep(0)
            wait_for_in_flight_samples()
            resumed_step_work()
            wait_for_in_flight_samples()

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        try:
            with tracer.trace("eager.parent", resource="eager-endpoint", span_type=ext.SpanTypes.WEB):
                await asyncio.create_task(
                    child(),
                    name="eager-child",
                    context=contextvars.Context(),  # type: ignore[call-arg]
                )
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
