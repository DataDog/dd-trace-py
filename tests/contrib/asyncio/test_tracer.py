"""Ensure that the tracer works with asynchronous executions within the same ``IOLoop``."""
import asyncio

import pytest

from ddtrace.contrib.asyncio.compat import asyncio_current_task


def test_get_call_context_twice(tracer):
    # it should return the same Context if called twice
    assert tracer.current_trace_context() == tracer.current_trace_context()


def test_trace_coroutine(tracer):
    # it should use the task context when invoked in a coroutine
    with tracer.trace("coroutine") as span:
        span.resource = "base"

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    assert "coroutine" == traces[0][0].name
    assert "base" == traces[0][0].resource


@pytest.mark.asyncio
async def test_trace_multiple_coroutines(tracer):
    # if multiple coroutines have nested tracing, they must belong
    # to the same trace
    async def coro():
        # another traced coroutine
        with tracer.trace("coroutine_2"):
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


def test_event_loop_exception(tracer):
    # it should handle a loop exception
    asyncio.set_event_loop(None)
    ctx = tracer.current_trace_context()
    assert ctx is None


def test_context_task_none(tracer):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # it should handle the case where a Task is not available
    # Note: the @pytest.mark.asyncio is missing to simulate an execution
    # without a Task
    task = asyncio_current_task()
    # the task is not available
    assert task is None

    ctx = tracer.current_trace_context()
    assert ctx is None


@pytest.mark.asyncio
async def test_exception(tracer):
    async def f1():
        with tracer.trace("f1"):
            raise Exception("f1 error")

    with pytest.raises(Exception):
        await f1()
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert 1 == span.error
    assert "f1 error" == span.get_tag("error.msg")
    assert "Exception: f1 error" in span.get_tag("error.stack")


@pytest.mark.asyncio
async def test_nested_exceptions(tracer):
    async def f1():
        with tracer.trace("f1"):
            raise Exception("f1 error")

    async def f2():
        with tracer.trace("f2"):
            await f1()

    with pytest.raises(Exception):
        await f2()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 2 == len(spans)
    span = spans[0]
    assert "f2" == span.name
    assert 1 == span.error  # f2 did not catch the exception
    assert "f1 error" == span.get_tag("error.msg")
    assert "Exception: f1 error" in span.get_tag("error.stack")
    span = spans[1]
    assert "f1" == span.name
    assert 1 == span.error
    assert "f1 error" == span.get_tag("error.msg")
    assert "Exception: f1 error" in span.get_tag("error.stack")


@pytest.mark.asyncio
async def test_handled_nested_exceptions(tracer):
    async def f1():
        with tracer.trace("f1"):
            raise Exception("f1 error")

    async def f2():
        with tracer.trace("f2"):
            try:
                await f1()
            except Exception:
                pass

    await f2()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 2 == len(spans)
    span = spans[0]
    assert "f2" == span.name
    assert 0 == span.error  # f2 caught the exception
    span = spans[1]
    assert "f1" == span.name
    assert 1 == span.error
    assert "f1 error" == span.get_tag("error.msg")
    assert "Exception: f1 error" in span.get_tag("error.stack")


@pytest.mark.asyncio
async def test_trace_multiple_calls(tracer):
    # create multiple futures so that we expect multiple
    # traces instead of a single one (helper not used)
    async def coro():
        # another traced coroutine
        with tracer.trace("coroutine"):
            await asyncio.sleep(0.01)

    for _ in range(10):
        await coro()

    traces = tracer.pop_traces()
    assert 10 == len(traces)
    assert 1 == len(traces[0])
    assert "coroutine" == traces[0][0].name


@pytest.mark.asyncio
async def test_wrapped_coroutine(tracer):
    @tracer.wrap("f1")
    async def f1():
        await asyncio.sleep(0.25)

    await f1()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert span.duration > 0.25, "span.duration={}".format(span.duration)
