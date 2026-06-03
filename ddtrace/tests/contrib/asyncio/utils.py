import asyncio
from functools import wraps
import logging
import sys

from tests.utils import TracerTestCase


log = logging.getLogger(__name__)


class AsyncioTestCase(TracerTestCase):
    """
    Base TestCase for asyncio framework that setup a new loop
    for each test, preserving the original (not started) main
    loop.
    """

    def setUp(self):
        super(AsyncioTestCase, self).setUp()
        try:
            # each test must have its own event loop
            self._main_loop = asyncio.get_event_loop()
        except RuntimeError:
            log.info("Couldn't find existing event loop")
            self._main_loop = None
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        super(AsyncioTestCase, self).tearDown()

        if self._main_loop is not None:
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

    @wraps(f)
    def wrapper(*args, **kwargs):
        if sys.version_info >= (3, 11):
            future = f(*args, **kwargs)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(future)
            loop.close()
        else:
            coro = asyncio.coroutine(f)
            future = coro(*args, **kwargs)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(future)
            loop.close()

    return wrapper
