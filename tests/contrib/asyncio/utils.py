import asyncio

from functools import wraps, partial

from ddtrace.contrib.asyncio import context_provider

from ...base import BaseTracerTestCase


class AsyncioTestCase(BaseTracerTestCase):
    """
    Base TestCase for asyncio framework that setup a new loop
    for each test, preserving the original (not started) main
    loop.
    """
    def setUp(self):
        super(AsyncioTestCase, self).setUp()

        self.tracer.configure(context_provider=context_provider)

        # each test must have its own event loop
        self._main_loop = asyncio.get_event_loop()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        super(AsyncioTestCase, self).tearDown()

        # restore the main loop
        asyncio.set_event_loop(self._main_loop)
        self.loop = None
        self._main_loop = None


def mark_asyncio(f, close_loop: bool = True):
    """
    Test decorator that wraps a function so that it can be executed
    as an asynchronous coroutine. This uses the event loop set in the
    ``TestCase`` class, and runs the loop until it's completed.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(future)

        if close_loop:
            loop.close()
    return wrapper


# This behaves similarly to mark_asyncio but does not close the loop at the end of the test
mark_asyncio_no_close = partial(mark_asyncio, close_loop=False)
