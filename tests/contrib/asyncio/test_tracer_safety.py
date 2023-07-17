import asyncio

import pytest

from ddtrace.contrib.asyncio.compat import asyncio_current_task
from ddtrace.internal.compat import CONTEXTVARS_IS_AVAILABLE
from ddtrace.provider import DefaultContextProvider


pytestmark = pytest.mark.skipif(
    CONTEXTVARS_IS_AVAILABLE, reason="No configuration is necessary when contextvars available."
)


@pytest.mark.asyncio
async def test_get_call_context(tracer):
    tracer.configure(context_provider=DefaultContextProvider())
    ctx = tracer.current_trace_context()
    assert ctx is None
    # test that it behaves the wrong way
    task = asyncio_current_task()
    assert task
    task_ctx = getattr(task, "__datadog_context", None)
    assert task_ctx is None


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
async def test_trace_multiple_calls(tracer):
    tracer.configure(context_provider=DefaultContextProvider())

    async def coro():
        # another traced coroutine
        with tracer.trace("coroutine"):
            await asyncio.sleep(0.01)

    # partial flushing is enabled, ensure the number of spans generated is less than 500
    futures = [asyncio.ensure_future(coro()) for x in range(400)]
    for future in futures:
        await future

    # the trace is wrong but the Context is finished
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 400 == len(traces[0])
