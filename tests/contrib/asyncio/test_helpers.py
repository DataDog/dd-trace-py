import asyncio

from nose.tools import eq_, ok_

from ddtrace.contrib.asyncio.helpers import ensure_future
from .utils import AsyncioTestCase, mark_asyncio


class TestAsyncioHelpers(AsyncioTestCase):
    """
    Ensure that helpers set the ``Context`` properly when creating
    new ``Task`` or threads.
    """
    @mark_asyncio
    def test_ensure_future(self):
        # the wrapper should create a new Future that has the Context attached
        async def future_work():
            # the ctx is available in this task
            ctx = self.tracer.get_call_context()
            eq_(1, len(ctx._trace))
            eq_('coroutine', ctx._trace[0].name)
            return ctx._trace[0].name

        span = self.tracer.trace('coroutine')
        # schedule future work and wait for a result
        delayed_task = ensure_future(future_work())
        result = yield from asyncio.wait_for(delayed_task, timeout=1)
        eq_('coroutine', result)
