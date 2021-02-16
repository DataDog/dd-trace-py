import asyncio

import pytest

from ddtrace.compat import CONTEXTVARS_IS_AVAILABLE
from ddtrace.context import Context
from ddtrace.contrib.asyncio import helpers

from .utils import AsyncioTestCase
from .utils import mark_asyncio


@pytest.mark.skipif(CONTEXTVARS_IS_AVAILABLE, reason="only applicable to legacy asyncio integration")
class TestAsyncioHelpers(AsyncioTestCase):
    """
    Ensure that helpers set the ``Context`` properly when creating
    new ``Task`` or threads.
    """

    @mark_asyncio
    def test_set_call_context(self):
        # a different Context is set for the current logical execution
        task = asyncio.Task.current_task()
        ctx = Context()
        helpers.set_call_context(task, ctx)
        assert ctx == self.tracer.get_call_context()

    @mark_asyncio
    def test_ensure_future(self):
        # the wrapper should create a new Future that has the Context attached
        @asyncio.coroutine
        def future_work():
            return self.tracer.trace("child")

        s1 = self.tracer.trace("coroutine")
        # schedule future work and wait for a result
        delayed_task = helpers.ensure_future(future_work(), tracer=self.tracer)
        s2 = yield from asyncio.wait_for(delayed_task, timeout=1)
        s2.finish()
        s1.finish()
        assert s2.name == "child"
        assert s1.parent_id is None
        assert s2.parent_id == s1.span_id
        assert s1.trace_id == s2.trace_id

    @mark_asyncio
    def test_run_in_executor_proxy(self):
        # the wrapper should pass arguments and results properly
        def future_work(number, name):
            assert 42 == number
            assert "john" == name
            return True

        future = helpers.run_in_executor(self.loop, None, future_work, 42, "john", tracer=self.tracer)
        result = yield from future
        assert result

    @mark_asyncio
    def test_run_in_executor_traces(self):
        # the wrapper should create a different Context when the Thread
        # is started; the new Context creates a new trace
        def future_work():
            # the Context is empty but the reference to the latest
            # span is here to keep the parenting
            return self.tracer.trace("child")

        span = self.tracer.trace("coroutine")
        future = helpers.run_in_executor(self.loop, None, future_work, tracer=self.tracer)
        span.finish()
        s2 = yield from future
        s2.finish()
        assert s2.name == "child"
        assert s2.trace_id == span.trace_id
        assert span.parent_id is None
        assert s2.parent_id == span.span_id

    @mark_asyncio
    def test_create_task(self):
        # the helper should create a new Task that has the Context attached
        @asyncio.coroutine
        def future_work():
            child_span = self.tracer.trace("child_task")
            return child_span

        root_span = self.tracer.trace("main_task")
        # schedule future work and wait for a result
        task = helpers.create_task(future_work())
        result = yield from task
        assert result.name == "child_task"
        assert root_span.trace_id == result.trace_id
        assert root_span.span_id == result.parent_id
