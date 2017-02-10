import asyncio

from aiohttp.test_utils import AioHTTPTestCase
from ddtrace.contrib.aiohttp.middlewares import TraceMiddleware

from .app.web import setup_app
from ..asyncio.utils import get_dummy_async_tracer


class TraceTestCase(AioHTTPTestCase):
    """
    Base class that provides a valid ``aiohttp`` application with
    the async tracer.
    """
    async def get_app(self, loop):
        """
        Override the get_app method to return the test application
        """
        # create the app with the testing loop
        app = setup_app(loop)
        asyncio.set_event_loop(loop)
        # trace the app
        self.tracer = get_dummy_async_tracer()
        TraceMiddleware(app, self.tracer)
        return app
