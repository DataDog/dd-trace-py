import asyncio
import sys

import pytest

from ddtrace.contrib.asyncio.patch import patch
from ddtrace.contrib.asyncio.patch import unpatch
from ddtrace.vendor import wrapt

from .utils import AsyncioTestCase


class CustomEventLoop(asyncio.BaseEventLoop):
    def create_task(self, coro):
        pass


@pytest.mark.skipif(sys.version_info >= (3, 7, 0), reason="Not relevant when contextvars are available")
class TestAsyncioPatch(AsyncioTestCase):
    """Ensure that asyncio patching works for event loops"""

    def tearDown(self):
        unpatch()
        super(TestAsyncioPatch, self).tearDown()

    def test_custom_event_loop(self):
        loop = CustomEventLoop()
        self.assertFalse(isinstance(loop.create_task, wrapt.ObjectProxy))
        asyncio.set_event_loop(loop)
        patch()
        self.assertTrue(isinstance(loop.create_task, wrapt.ObjectProxy))
        unpatch()
        self.assertFalse(isinstance(loop.create_task, wrapt.ObjectProxy))

    def test_event_loop(self):
        patch()
        loop = asyncio.get_event_loop()
        self.assertIsInstance(loop.create_task, wrapt.ObjectProxy)

    def test_new_loop(self):
        patch()
        loop = asyncio.new_event_loop()
        self.assertIsInstance(loop.create_task, wrapt.ObjectProxy)

    def test_after_set_event_loop(self):
        loop = asyncio.new_event_loop()
        self.assertNotIsInstance(loop.create_task, wrapt.ObjectProxy)
        asyncio.set_event_loop(loop)
        patch()
        self.assertIsInstance(loop.create_task, wrapt.ObjectProxy)
        unpatch()
        self.assertNotIsInstance(loop.create_task, wrapt.ObjectProxy)
