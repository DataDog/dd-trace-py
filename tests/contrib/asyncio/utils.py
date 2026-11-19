from unittest import IsolatedAsyncioTestCase

from tests.utils import TracerTestCase


class AsyncioTestCase(TracerTestCase, IsolatedAsyncioTestCase):
    """Provide tracer assertions with unittest-owned async setup and cleanup."""
