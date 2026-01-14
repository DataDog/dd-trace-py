"""
Test that IAST works correctly with anyio's multi-threaded async operations.

The issue was that anyio creates multiple threads for async operations, and when
these threads accessed the Initializer's object pools concurrently, race conditions
occurred because the pools weren't thread-safe. This led to segfaults.

This test verifies the fix by simulating anyio-like concurrent access patterns.
"""

import pytest


# Skip all tests in this module if anyio is not installed
pytest.importorskip("anyio")

import anyio

from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject


@pytest.mark.asyncio
async def test_anyio_concurrent_taint_operations():
    """Test IAST with anyio's concurrent async operations."""

    oce.reconfigure()
    _iast_start_request()

    async def async_taint_worker(worker_id, iterations=50):
        """Async worker that creates tainted objects."""
        for i in range(iterations):
            # This exercises the thread-safe object pool
            data = taint_pyobject(
                f"anyio_worker_{worker_id}_data_{i}",
                f"anyio_worker_{worker_id}_source_{i}",
                f"anyio_worker_{worker_id}_value_{i}",
                OriginType.PARAMETER,
            )
            # Yield to allow other tasks to run
            await anyio.sleep(0.001)
            del data

    # Create multiple concurrent tasks
    async with anyio.create_task_group() as tg:
        for i in range(10):
            tg.start_soon(async_taint_worker, i)

    # If we got here without segfault, the thread-safety fix worked
    assert True, "Anyio concurrent operations completed successfully"


@pytest.mark.asyncio
async def test_anyio_memory_streams():
    """Test IAST with anyio memory streams (the specific failing case)."""

    oce.reconfigure()
    _iast_start_request()

    async def producer(send_stream):
        """Producer that sends tainted data."""
        for i in range(100):
            data = taint_pyobject(
                f"stream_data_{i}",
                f"stream_source_{i}",
                f"stream_value_{i}",
                OriginType.PARAMETER,
            )
            await send_stream.send(data)
        await send_stream.aclose()

    async def consumer(receive_stream):
        """Consumer that receives tainted data."""
        async for item in receive_stream:
            # Process the tainted item
            _ = item

    # This pattern is similar to what Starlette/FastAPI use with anyio
    send_stream, receive_stream = anyio.create_memory_object_stream()

    async with anyio.create_task_group() as tg:
        tg.start_soon(producer, send_stream)
        tg.start_soon(consumer, receive_stream)

    # If we got here without segfault, the thread-safety fix worked
    assert True, "Anyio memory stream operations completed successfully"


@pytest.mark.asyncio
async def test_anyio_event_creation():
    """Test IAST with anyio Event creation (the specific line in the stack trace)."""

    oce.reconfigure()
    _iast_start_request()

    async def worker_with_events(worker_id):
        """Worker that creates events and tainted objects."""
        for i in range(50):
            # Create an anyio Event (this was failing in the stack trace)
            event = anyio.Event()

            # Create tainted data while event exists
            data = taint_pyobject(
                f"event_worker_{worker_id}_data_{i}",
                f"event_worker_{worker_id}_source_{i}",
                f"event_worker_{worker_id}_value_{i}",
                OriginType.PARAMETER,
            )

            # Set and wait on event
            event.set()
            await event.wait()

            del data

    # Run multiple workers concurrently
    async with anyio.create_task_group() as tg:
        for i in range(10):
            tg.start_soon(worker_with_events, i)

    # If we got here without segfault, the thread-safety fix worked
    assert True, "Anyio event creation with IAST completed successfully"


def test_anyio_blocking_portal():
    """Test IAST with anyio's BlockingPortal (runs async code from sync context)."""

    oce.reconfigure()
    _iast_start_request()

    async def async_operation(value):
        """Async operation that taints data."""
        data = taint_pyobject(
            f"portal_{value}",
            f"portal_source_{value}",
            f"portal_value_{value}",
            OriginType.PARAMETER,
        )
        await anyio.sleep(0.01)
        return data

    # BlockingPortal allows running async code from synchronous code
    # This can trigger multi-threading issues
    with anyio.from_thread.start_blocking_portal() as portal:
        results = []
        for i in range(20):
            result = portal.call(async_operation, i)
            results.append(result)

    assert len(results) == 20, "All portal operations should complete"
