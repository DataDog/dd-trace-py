import pkg_resources

import aiohttp
from aiohttp.test_utils import AioHTTPTestCase

from .app.web import setup_app
from ddtrace.contrib.asyncio import context_provider
from ...base import BaseTracerTestCase


if pkg_resources.parse_version(aiohttp.__version__) >= pkg_resources.parse_version('3.3.0'):
    AIOHTTP_33x = True
else:
    AIOHTTP_33x = False


class TraceTestCase(BaseTracerTestCase, AioHTTPTestCase):
    """
    Base class that provides a valid ``aiohttp`` application with
    the async tracer.
    """

    def enable_tracing(self):
        pass

    def disable_tracing(self):
        pass

    def tearDown(self):
        # unpatch the aiohttp_jinja2 module
        super(TraceTestCase, self).tearDown()
        self.disable_tracing()

    def get_app(self, loop=None):
        """
        Override the get_app method to return the test application
        """
        # aiohttp 2.0+ stores the loop instance in self.loop; for
        # backward compatibility, we should expect a `loop` argument
        # However since 3.3+ it's been deprecated
        if not AIOHTTP_33x:
            loop = loop or self.loop

        # create the app with the testing loop
        self.app = setup_app(loop)
        # trace the app
        self.tracer.configure(context_provider=context_provider)
        self.enable_tracing()
        return self.app
