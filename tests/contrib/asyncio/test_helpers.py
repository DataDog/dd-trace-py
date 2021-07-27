"""Ensure that helpers set the ``Context`` properly when creating new ``Task`` or threads."""
import asyncio

import pytest

from ddtrace.context import Context
from ddtrace.contrib.asyncio import context_provider
from ddtrace.contrib.asyncio import helpers
from ddtrace.internal.compat import CONTEXTVARS_IS_AVAILABLE


pytestmark = pytest.mark.skipif(CONTEXTVARS_IS_AVAILABLE, reason="only applicable to legacy asyncio integration")


@pytest.mark.asyncio
async def test_set_call_context(tracer):
    tracer.configure(context_provider=context_provider)
    # a different Context is set for the current logical execution
    task = asyncio.Task.current_task()
    ctx = Context()
    helpers.set_call_context(task, ctx)
    assert ctx == tracer.current_trace_context()


@pytest.mark.asyncio
async def test_ensure_future(tracer):
    # the wrapper should create a new Future that has the Context attached
    async def future_work():
        return tracer.trace("child")

    s1 = tracer.trace("coroutine")
    # schedule future work and wait for a result
    delayed_task = helpers.ensure_future(future_work(), tracer=tracer)
    s2 = await asyncio.wait_for(delayed_task, timeout=1)
    s2.finish()
    s1.finish()
    assert s2.name == "child"
    assert s1.parent_id is None
    assert s2.parent_id == s1.span_id
    assert s1.trace_id == s2.trace_id


@pytest.mark.asyncio
async def test_run_in_executor_proxy(event_loop, tracer):
    # the wrapper should pass arguments and results properly
    def future_work(number, name):
        assert 42 == number
        assert "john" == name
        return True

    future = helpers.run_in_executor(event_loop, None, future_work, 42, "john", tracer=tracer)
    result = await future
    assert result


@pytest.mark.asyncio
async def test_run_in_executor_traces(event_loop, tracer):
    # the wrapper should create a different Context when the Thread
    # is started; the new Context creates a new trace
    def future_work():
        # the Context is empty but the reference to the latest
        # span is here to keep the parenting
        return tracer.trace("child")

    span = tracer.trace("coroutine")
    future = helpers.run_in_executor(event_loop, None, future_work, tracer=tracer)
    span.finish()
    s2 = await future
    s2.finish()
    assert s2.name == "child"
    assert s2.trace_id == span.trace_id
    assert span.parent_id is None
    assert s2.parent_id == span.span_id


@pytest.mark.asyncio
async def test_create_task(tracer):
    # the helper should create a new Task that has the Context attached
    async def future_work():
        child_span = tracer.trace("child_task")
        return child_span

    root_span = tracer.trace("main_task")
    # schedule future work and wait for a result
    task = helpers.create_task(future_work())
    result = await task
    assert result.name == "child_task"
    assert root_span.trace_id == result.trace_id
    assert root_span.span_id == result.parent_id
