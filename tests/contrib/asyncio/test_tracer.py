"""Ensure that the tracer works with asynchronous executions within the same ``IOLoop``."""

import asyncio
import os
import re

import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.asyncio.patch import patch
from ddtrace.contrib.internal.asyncio.patch import unpatch


@pytest.fixture(autouse=True)
def patch_asyncio():
    patch()
    yield
    unpatch()


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


@pytest.mark.asyncio
async def test_exception(tracer):
    async def f1():
        with tracer.trace("f1"):
            raise Exception("f1 error")

    with pytest.raises(Exception, match="f1 error"):
        await f1()
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert 1 == span.error
    assert "f1 error" == span.get_tag(ERROR_MSG)
    assert "Exception: f1 error" in span.get_tag("error.stack")


@pytest.mark.asyncio
async def test_nested_exceptions(tracer):
    async def f1():
        with tracer.trace("f1"):
            raise Exception("f1 error")

    async def f2():
        with tracer.trace("f2"):
            await f1()

    with pytest.raises(Exception, match="f1 error"):
        await f2()

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 2 == len(spans)
    span = spans[0]
    assert "f2" == span.name
    assert 1 == span.error  # f2 did not catch the exception
    assert "f1 error" == span.get_tag(ERROR_MSG)
    assert "Exception: f1 error" in span.get_tag("error.stack")
    span = spans[1]
    assert "f1" == span.name
    assert 1 == span.error
    assert "f1 error" == span.get_tag(ERROR_MSG)
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
    assert "f1 error" == span.get_tag(ERROR_MSG)
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


def test_asyncio_scheduled_tasks_parenting(tracer):
    async def task(i):
        with tracer.trace(f"task {i}"):
            await asyncio.sleep(0.1)

    @tracer.wrap()
    async def runner():
        await task(1)
        t = asyncio.create_task(task(2))
        return t

    async def test():
        await runner()

    asyncio.run(test())

    spans = tracer.get_spans()
    assert len(spans) == 3
    assert spans[0].trace_id == spans[1].trace_id == spans[2].trace_id


def test_asyncio_scheduled_tasks_debug_logs(run_python_code_in_subprocess):
    code = """
import ddtrace

ddtrace.patch(asyncio=True)
import asyncio
import time
import pytest


async def my_function():
    time.sleep(2)

if __name__ == "__main__":
    asyncio.run(my_function())
"""
    env = os.environ.copy()
    env["PYTHONASYNCIODEBUG"] = "1"
    out, err, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, err + out

    pattern = rb"Executing <Task finished name=\'Task-1\' coro=<my_function\(\) done, "
    rb"defined at .*/dd-trace-py/ddtrace/contrib/internal/asyncio/patch.py:.* result=None "
    rb"created at .*/dd-trace-py/ddtrace/contrib/internal/asyncio/patch.py:.* took .* seconds"
    match = re.match(pattern, err)
    assert match, err


@pytest.mark.asyncio
async def test_wrapped_generator(tracer):
    @tracer.wrap("decorated_generator", service="s", resource="r", span_type="t")
    async def f(tag_name, tag_value):
        # make sure we can still set tags
        span = tracer.current_span()
        span.set_tag(tag_name, tag_value)

        for i in range(3):
            yield i

    result = [item async for item in f("a", "b")]
    assert result == [0, 1, 2]

    traces = tracer.pop_traces()

    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]

    assert span.name == "decorated_generator"
    assert span.service == "s"
    assert span.resource == "r"
    assert span.span_type == "t"
    assert span.get_tag("a") == "b"
