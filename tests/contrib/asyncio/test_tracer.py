"""Ensure that the tracer works with asynchronous executions within the same ``IOLoop``."""

import asyncio
from contextlib import asynccontextmanager
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


def test_trace_coroutine(tracer, test_spans):
    # it should use the task context when invoked in a coroutine
    with tracer.trace("coroutine") as span:
        span.resource = "base"

    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    assert "coroutine" == traces[0][0].name
    assert "base" == traces[0][0].resource


@pytest.mark.asyncio
async def test_trace_multiple_coroutines(tracer, test_spans):
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
    traces = test_spans.pop_traces()
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
async def test_exception(tracer, test_spans):
    async def f1():
        with tracer.trace("f1"):
            raise Exception("f1 error")

    with pytest.raises(Exception, match="f1 error"):
        await f1()
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert 1 == span.error
    assert "f1 error" == span.get_tag(ERROR_MSG)
    assert "Exception: f1 error" in span.get_tag("error.stack")


@pytest.mark.asyncio
async def test_nested_exceptions(tracer, test_spans):
    async def f1():
        with tracer.trace("f1"):
            raise Exception("f1 error")

    async def f2():
        with tracer.trace("f2"):
            await f1()

    with pytest.raises(Exception, match="f1 error"):
        await f2()

    traces = test_spans.pop_traces()
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
async def test_handled_nested_exceptions(tracer, test_spans):
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

    traces = test_spans.pop_traces()
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
async def test_trace_multiple_calls(tracer, test_spans):
    # create multiple futures so that we expect multiple
    # traces instead of a single one (helper not used)
    async def coro():
        # another traced coroutine
        with tracer.trace("coroutine"):
            await asyncio.sleep(0.01)

    for _ in range(10):
        await coro()

    traces = test_spans.pop_traces()
    assert 10 == len(traces)
    assert 1 == len(traces[0])
    assert "coroutine" == traces[0][0].name


@pytest.mark.asyncio
async def test_wrapped_coroutine(tracer, test_spans):
    @tracer.wrap("f1")
    async def f1():
        await asyncio.sleep(0.25)

    await f1()

    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]
    assert span.duration > 0.25, "span.duration={}".format(span.duration)


def test_asyncio_scheduled_tasks_parenting(tracer, test_spans):
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

    spans = test_spans.spans
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
async def test_wrapped_generator(tracer, test_spans):
    @tracer.wrap("decorated_generator", service="s", resource="r", span_type="t")
    async def f(tag_name, tag_value):
        # make sure we can still set tags
        span = tracer.current_span()
        span.set_tag(tag_name, tag_value)

        for i in range(3):
            yield i

    result = [item async for item in f("a", "b")]
    assert result == [0, 1, 2]

    traces = test_spans.pop_traces()

    assert 1 == len(traces)
    spans = traces[0]
    assert 1 == len(spans)
    span = spans[0]

    assert span.name == "decorated_generator"
    assert span.service == "s"
    assert span.resource == "r"
    assert span.span_type == "t"
    assert span.get_tag("a") == "b"


@pytest.mark.asyncio
async def test_wrapped_async_gen_asynccontextmanager(tracer, test_spans):
    # @tracer.wrap() on an async generator must compose with
    # contextlib.asynccontextmanager so it can be driven via ``async with``.
    events = []

    @asynccontextmanager
    @tracer.wrap("managed")
    async def managed():
        events.append("enter")
        try:
            yield "resource"
        finally:
            events.append("cleanup")

    async with managed() as resource:
        assert resource == "resource"
        events.append("body")

    assert events == ["enter", "body", "cleanup"]

    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    assert traces[0][0].name == "managed"


@pytest.mark.asyncio
async def test_wrapped_async_gen_cleanup_on_exception(tracer, test_spans):
    # When the consumer raises, the exception must propagate AND the wrapped
    # generator's ``finally`` block must still run (regression: the old
    # ``async for ... yield`` wrapper skipped the inner cleanup).
    events = []

    @asynccontextmanager
    @tracer.wrap("managed")
    async def managed():
        events.append("enter")
        try:
            yield "resource"
        finally:
            events.append("cleanup")

    with pytest.raises(ValueError, match="boom"):
        async with managed():
            events.append("body")
            raise ValueError("boom")

    assert events == ["enter", "body", "cleanup"]


@pytest.mark.asyncio
async def test_wrapped_async_gen_early_close(tracer, test_spans):
    # Closing the wrapped generator before it is exhausted must forward
    # ``aclose`` to the inner generator so its ``finally`` runs, without raising
    # "aclose(): asynchronous generator is already running".
    events = []

    @tracer.wrap("counter")
    async def counter():
        try:
            for i in range(10):
                yield i
        finally:
            events.append("cleanup")

    gen = counter()
    seen = [await gen.__anext__(), await gen.__anext__()]
    await gen.aclose()

    assert seen == [0, 1]
    assert events == ["cleanup"]

    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert traces[0][0].name == "counter"


@pytest.mark.asyncio
async def test_wrapped_async_gen_send(tracer, test_spans):
    # Values sent into the wrapped generator via ``asend`` must be forwarded
    # to the inner generator.
    received = []

    @tracer.wrap("echo")
    async def echo():
        while True:
            value = yield
            received.append(value)

    gen = echo()
    await gen.asend(None)  # prime
    await gen.asend("a")
    await gen.asend("b")
    await gen.aclose()

    assert received == ["a", "b"]


@pytest.mark.asyncio
async def test_wrapped_async_gen_classmethod_asynccontextmanager(tracer, test_spans):
    # The exact shape that triggered the original bug: a classmethod async
    # context manager whose body raises must propagate the exception AND run
    # the wrapped generator's ``finally``.
    events = []

    class Model:
        @classmethod
        @asynccontextmanager
        @tracer.wrap("model.conn")
        async def get_conn(cls):
            events.append("enter")
            try:
                yield "conn"
            finally:
                events.append("cleanup")

    with pytest.raises(ValueError):
        async with Model.get_conn() as conn:
            assert conn == "conn"
            events.append("body")
            raise ValueError("boom")

    assert events == ["enter", "body", "cleanup"]


@pytest.mark.asyncio
async def test_wrapped_async_gen_cancelled_error(tracer, test_spans):
    # asyncio.CancelledError (a BaseException, not Exception) must still
    # propagate and run the wrapped generator's ``finally``.
    events = []

    @asynccontextmanager
    @tracer.wrap("managed")
    async def managed():
        try:
            yield "resource"
        finally:
            events.append("cleanup")

    with pytest.raises(asyncio.CancelledError):
        async with managed():
            raise asyncio.CancelledError()

    assert events == ["cleanup"]


@pytest.mark.asyncio
async def test_wrapped_async_gen_athrow_yields(tracer, test_spans):
    """Inner gen catches the thrown exception and yields a recovery value."""

    @tracer.wrap("resilient")
    async def resilient():
        value = 0
        while True:
            try:
                yield value
            except ValueError:
                yield -1  # recovery yield, then loop continues
            value += 1

    gen = resilient()
    assert await gen.asend(None) == 0
    assert await gen.asend(None) == 1
    recovery = await gen.athrow(ValueError("oops"))  # should yield -1, not raise
    assert recovery == -1
    assert await gen.asend(None) == 2  # resumes normally after recovery
    await gen.aclose()
