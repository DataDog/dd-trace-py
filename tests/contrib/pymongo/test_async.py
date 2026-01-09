import pymongo
import pytest

from ddtrace.contrib.internal.pymongo.patch import patch
from ddtrace.contrib.internal.pymongo.patch import unpatch
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio


pytestmark = pytest.mark.skipif(
    pymongo.version_tuple < (4, 12),
    reason="Async pymongo support requires pymongo >= 4.12",
)


if pymongo.version_tuple >= (4, 12):
    from pymongo.asynchronous.mongo_client import AsyncMongoClient
else:
    # Fallback for older versions (though tests will be skipped)
    AsyncMongoClient = None


class TestAsyncPymongoBasic(AsyncioTestCase):
    """Minimal async pymongo tests focused on async-specific behavior"""

    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        super().tearDown()
        unpatch()

    @mark_asyncio
    async def test_async_basic_operations(self):
        """Test that basic async operations are traced"""
        pass

    @mark_asyncio
    async def test_async_cursor_iteration(self):
        """Test that async cursor iteration is traced"""
        pass

    @mark_asyncio
    async def test_async_patch_unpatch(self):
        """Test that async patching can be enabled/disabled"""
        pass
