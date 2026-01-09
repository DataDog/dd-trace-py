import pymongo
import pytest

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.pymongo.patch import patch
from ddtrace.contrib.internal.pymongo.patch import unpatch
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
from tests.utils import DummyTracer

from ..config import MONGO_CONFIG


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
        """Test that basic async operations are traced - simple debug test"""
        import sys
        print("DEBUG: Starting test", file=sys.stderr)
        tracer = DummyTracer()
        client = None
        try:
            print("DEBUG: Creating AsyncMongoClient", file=sys.stderr)
            client = AsyncMongoClient(port=MONGO_CONFIG["port"])
            print(f"DEBUG: Client created: {client}", file=sys.stderr)
            
            # Set pin on client with tracer (like sync tests do)
            # This will propagate to topology through setup_mongo_client_pin mechanism
            Pin.get_from(client)._clone(tracer=tracer).onto(client)
            # Also ensure pin is on topology with tracer
            Pin.get_from(client)._clone(tracer=tracer).onto(client._topology)

            # Perform a simple operation
            print("DEBUG: Getting database", file=sys.stderr)
            db = client["testdb"]
            print("DEBUG: Dropping collection", file=sys.stderr)
            await db.drop_collection("test_collection")
            print("DEBUG: Collection dropped", file=sys.stderr)

            # Check that spans were created
            spans = tracer.pop()
            print(f"DEBUG: Number of spans: {len(spans)}", file=sys.stderr)
            for i, span in enumerate(spans):
                print(f"DEBUG: Span {i}: name={span.name}, service={span.service}", file=sys.stderr)
            
            assert len(spans) > 0, f"Expected at least one span to be created, got {len(spans)} spans"

            # Verify span properties
            for span in spans:
                assert span.service is not None, f"Span {span.name} has no service"
                assert span.name is not None, f"Span has no name"
            print("DEBUG: Test passed!", file=sys.stderr)
        except Exception as e:
            print(f"DEBUG: Exception occurred: {type(e).__name__}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            raise
        finally:
            # Properly close async client
            if client:
                print("DEBUG: Closing client", file=sys.stderr)
                await client.close()
                # Give async tasks time to clean up
                import asyncio
                await asyncio.sleep(0.1)
                print("DEBUG: Client closed", file=sys.stderr)

    @mark_asyncio
    async def test_async_cursor_iteration(self):
        """Test that async cursor iteration is traced"""
        pass

    @mark_asyncio
    async def test_async_patch_unpatch(self):
        """Test that async patching can be enabled/disabled"""
        pass
