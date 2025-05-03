import asyncio
import time

import pytest

from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.contrib.internal.asyncio.patch import patch
from ddtrace.contrib.internal.asyncio.patch import unpatch
from ddtrace.trace import Context
from tests.opentracer.utils import init_tracer


_orig_create_task = asyncio.BaseEventLoop.create_task


def test_event_loop_unpatch(tracer):
    patch()
    # ensures that the event loop can be unpatched
    unpatch()
    assert isinstance(tracer.context_provider, DefaultContextProvider)
    assert asyncio.BaseEventLoop.create_task == _orig_create_task


@pytest.mark.asyncio
async def test_event_loop_double_patch(tracer):
    # ensures that double patching will not double instrument
    # the event loop
    patch()
    patch()
    await test_tasks_chaining(tracer)


@pytest.mark.asyncio
async def test_tasks_chaining(tracer):
    # ensures that the context is propagated between different tasks
    @tracer.wrap("spawn_task")
    async def coro_3():
        await asyncio.sleep(0.01)

    async def coro_2():
        # This will have a new context, first run will test that the
        # new context works correctly, second run will test if when we
        # pop off the last span on the context if it is still parented
        # correctly
        await coro_3()
        await coro_3()

    @tracer.wrap("main_task")
    async def coro_1():
        await asyncio.ensure_future(coro_2())

    await coro_1()

    traces = tracer.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 3
    main_task = spans[0]
    spawn_task1 = spans[1]
    spawn_task2 = spans[2]
    # check if the context has been correctly propagated
    assert spawn_task1.trace_id == main_task.trace_id
    assert spawn_task1.parent_id == main_task.span_id

    assert spawn_task2.trace_id == main_task.trace_id
    assert spawn_task2.parent_id == main_task.span_id


@pytest.mark.asyncio
async def test_concurrent_chaining(tracer):
    @tracer.wrap("f1")
    async def f1():
        await asyncio.sleep(0.01)

    @tracer.wrap("f2")
    async def f2():
        await asyncio.sleep(0.01)

    with tracer.trace("main_task"):
        await asyncio.gather(f1(), f2())
        # do additional synchronous work to confirm main context is
        # correctly handled
        with tracer.trace("main_task_child"):
            time.sleep(0.01)

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 4
    main_task = traces[0][0]
    child_1 = traces[0][1]
    child_2 = traces[0][2]
    main_task_child = traces[0][3]
    # check if the context has been correctly propagated
    assert child_1.trace_id == main_task.trace_id
    assert child_1.parent_id == main_task.span_id
    assert child_2.trace_id == main_task.trace_id
    assert child_2.parent_id == main_task.span_id
    assert main_task_child.trace_id == main_task.trace_id
    assert main_task_child.parent_id == main_task.span_id


@pytest.mark.asyncio
async def test_propagation_with_new_context(tracer):
    # ensures that if a new Context is activated, a trace
    # with the Context arguments is created
    ctx = Context(trace_id=100, span_id=101)
    tracer.context_provider.activate(ctx)

    with tracer.trace("async_task"):
        await asyncio.sleep(0.01)

    traces = tracer.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    span = traces[0][0]
    assert span.trace_id == 100
    assert span.parent_id == 101


@pytest.mark.asyncio
async def test_trace_multiple_coroutines_ot_outer(tracer):
    """OpenTracing version of test_trace_multiple_coroutines."""

    # if multiple coroutines have nested tracing, they must belong
    # to the same trace
    async def coro():
        # another traced coroutine
        with tracer.trace("coroutine_2"):
            return 42

    ot_tracer = init_tracer("asyncio_svc", tracer)
    with ot_tracer.start_active_span("coroutine_1"):
        value = await coro()

    # the coroutine has been called correctly
    assert 42 == value
    # a single trace has been properly reported
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 2 == len(traces[0])
    assert "coroutine_1" == traces[0][0].name
    assert "coroutine_2" == traces[0][1].name
    # the parenting is correct
    assert traces[0][0] == traces[0][1]._parent
    assert traces[0][0].trace_id == traces[0][1].trace_id


@pytest.mark.asyncio
async def test_trace_multiple_coroutines_ot_inner(tracer):
    """OpenTracing version of test_trace_multiple_coroutines."""
    # if multiple coroutines have nested tracing, they must belong
    # to the same trace
    ot_tracer = init_tracer("asyncio_svc", tracer)

    async def coro():
        # another traced coroutine
        with ot_tracer.start_active_span("coroutine_2"):
            return 42

    with tracer.trace("coroutine_1"):
        value = await coro()

    # the coroutine has been called correctly
    assert 42 == value
    # a single trace has been properly reported
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 2 == len(traces[0])
    assert "coroutine_1" == traces[0][0].name
    assert "coroutine_2" == traces[0][1].name
    # the parenting is correct
    assert traces[0][0] == traces[0][1]._parent
    assert traces[0][0].trace_id == traces[0][1].trace_id
