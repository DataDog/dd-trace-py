import asyncio

from ddtrace.contrib.asyncio import context_provider
from tests.utils import TracerTestCase


class AsyncioTestCase(TracerTestCase):
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
