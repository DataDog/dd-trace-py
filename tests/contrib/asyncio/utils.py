import asyncio

from unittest import TestCase

from ddtrace.contrib.asyncio.tracer import AsyncioTracer
from tests.test_tracer import DummyWriter


def get_dummy_async_tracer():
    """
    Returns the AsyncTracer instance with a DummyWriter
    """
    tracer = AsyncioTracer()
    tracer.writer = DummyWriter()
    return tracer


class AsyncioTestCase(TestCase):
    """
    Base TestCase for asyncio framework that setup a new loop
    for each test, preserving the original (not started) main
    loop.
    """
    def setUp(self):
        # each test must have its own event loop
        self._main_loop = asyncio.get_event_loop()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        # provide the AsyncioTracer
        self.tracer = get_dummy_async_tracer()

    def tearDown(self):
        # restore the main loop
        asyncio.set_event_loop(self._main_loop)
        self.loop = None
        self._main_loop = None


def mark_asyncio(f):
    """
    Test decorator that wraps a function so that it can be executed
    as an asynchronous coroutine. This uses the event loop set in the
    ``TestCase`` class, and runs the loop until it's completed.
    """
    def wrapper(*args, **kwargs):
        coro = asyncio.coroutine(f)
        future = coro(*args, **kwargs)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(future)
        loop.close()
    wrapper.__name__ = f.__name__
    return wrapper
