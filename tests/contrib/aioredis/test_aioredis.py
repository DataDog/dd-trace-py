import aioredis
import pytest

from ddtrace.contrib.aioredis.patch import aioredis_version
from ddtrace.contrib.aioredis.patch import patch
from ddtrace.contrib.aioredis.patch import unpatch
from ddtrace.vendor.wrapt import ObjectProxy
from tests.utils import override_config

from ..config import REDIS_CONFIG


@pytest.fixture
async def redis_client():
    r = await get_redis_instance()
    yield r


def get_redis_instance():
    if aioredis_version >= (2, 0):
        return aioredis.from_url('redis://localhost:%s' % REDIS_CONFIG["port"])
    return aioredis.create_redis(('localhost', 6379))


@pytest.fixture(autouse=True)
async def traced_aioredis(redis_client):
    await redis_client.flushall()

    patch()
    try:
        yield
    finally:
        unpatch()
    await redis_client.flushall()


def test_patching():
    """
    When patching aioredis library
        We wrap the correct methods
    When unpatching aioredis library
        We unwrap the correct methods
    """
    if aioredis_version >= (2, 0):
        assert isinstance(aioredis.client.Redis.execute_command, ObjectProxy)
        assert isinstance(aioredis.client.Redis.pipeline, ObjectProxy)
        assert isinstance(aioredis.client.Pipeline.pipeline, ObjectProxy)
        unpatch()
        assert not isinstance(aioredis.client.Redis.execute_command, ObjectProxy)
        assert not isinstance(aioredis.client.Redis.pipeline, ObjectProxy)
        assert not isinstance(aioredis.client.Pipeline.pipeline, ObjectProxy)
    else:
        assert isinstance(aioredis.Redis.execute, ObjectProxy)
        assert isinstance(aioredis.commands.Pipeline.execute, ObjectProxy)
        unpatch()
        assert not isinstance(aioredis.Redis.execute, ObjectProxy)
        assert not isinstance(aioredis.commands.Pipeline.execute, ObjectProxy)


# test return value for all requests
@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_basic_request(redis_client):
    val = await redis_client.get("cheese")
    assert val is None


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_override_service_name(redis_client):
    with override_config("aioredis", dict(service_name="myaioredis")):
        val = await redis_client.get("cheese")
        assert val is None
        await redis_client.set("cheese", "my-cheese")
        val = await redis_client.get("cheese")
        if isinstance(val, bytes):
            val = val.decode()
        assert val == "my-cheese"


# check if you can use pin to override details - like aredis test_opentracing,
# but it can be changed to be a snapshot testing case

"""
@pytest.mark.asyncio
async def test_execute_command(snapshot_context):
    with snapshot_context():
"""
