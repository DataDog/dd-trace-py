from contextlib import contextmanager

import aioredis
import pytest

from ddtrace.contrib.aioredis.patch import patch
from ddtrace.contrib.httpx.patch import unpatch
from ddtrace.vendor.wrapt import ObjectProxy
from tests.utils import override_config

from ..config import REDIS_CONFIG


@contextmanager
def get_redis_instance():
    r = aioredis.from_url(port=REDIS_CONFIG["port"])
    yield r


@pytest.fixture(autouse=True)
async def traced_aioredis():
    with get_redis_instance() as r:
        await r.flushall()

        patch()
        try:
            yield
        finally:
            unpatch()

        await r.flushall()


def test_patching():
    """
    When patching aioredis library
        We wrap the correct methods
    When unpatching aioredis library
        We unwrap the correct methods
    """
    assert isinstance(aioredis.client.Redis.execute_command, ObjectProxy)
    assert isinstance(aioredis.client.Redis.pipeline, ObjectProxy)
    assert isinstance(aioredis.client.Pipeline.pipeline, ObjectProxy)

    unpatch()
    assert not isinstance(aioredis.client.Redis.execute_command, ObjectProxy)
    assert not isinstance(aioredis.client.Redis.pipeline, ObjectProxy)
    assert not isinstance(aioredis.client.Pipeline.pipeline, ObjectProxy)


@pytest.mark.asyncio
async def test_basic_request(snapshot_context):
    with snapshot_context():
        with get_redis_instance() as r:
            await r.set("cheese", "my_cheese")


@pytest.mark.asyncio
async def test_override_service_name(snapshot_context):
    with override_config("aioredis", dict(service_name="myaioredis")):
        with snapshot_context():
            with get_redis_instance() as r:
                await r.get("cheese")


"""
@pytest.mark.asyncio
async def test_execute_command(snapshot_context):
    with snapshot_context():
"""
