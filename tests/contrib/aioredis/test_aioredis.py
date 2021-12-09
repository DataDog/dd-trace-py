import aioredis
import pytest

from ddtrace import Pin
from ddtrace import tracer
from ddtrace.contrib.aioredis.patch import aioredis_version
from ddtrace.contrib.aioredis.patch import patch
from ddtrace.contrib.aioredis.patch import unpatch
from ddtrace.vendor.wrapt import ObjectProxy
from tests.utils import override_config

from ..config import REDIS_CONFIG


@pytest.mark.asyncio
@pytest.fixture
async def redis_client():
    r = await get_redis_instance()
    yield r


def get_redis_instance():
    if aioredis_version >= (2, 0):
        return aioredis.from_url("redis://127.0.0.1:%s" % REDIS_CONFIG["port"])
    return aioredis.create_redis(("localhost", REDIS_CONFIG["port"]))


@pytest.mark.asyncio
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
        unpatch()
        assert not isinstance(aioredis.Redis.execute, ObjectProxy)


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_basic_request(redis_client):
    val = await redis_client.get("cheese")
    assert val is None


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_long_command(redis_client):
    length = 1000
    val_list = await redis_client.mget(*range(length))
    assert len(val_list) == length
    for val in val_list:
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


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_pin(redis_client):
    Pin.override(redis_client, service="my-aioredis")
    val = await redis_client.get("cheese")
    assert val is None


@pytest.mark.asyncio
@pytest.mark.snapshot(variants={"": aioredis_version >= (2, 0), "13": aioredis_version < (2, 0)})
async def test_pipeline_traced(redis_client):
    if aioredis_version >= (2, 0):
        p = await redis_client.pipeline(transaction=False)
        await p.set("blah", "boo")
        await p.set("foo", "bar")
        await p.get("blah")
        await p.get("foo")
    else:
        p = redis_client.pipeline()
        p.set("blah", "boo")
        p.set("foo", "bar")
        p.get("blah")
        p.get("foo")

    response_list = await p.execute()
    assert response_list[0] is True  # response from redis.set is OK if successfully pushed
    assert response_list[1] is True
    assert (
        response_list[2].decode() == "boo"
    )  # response from hset is 'Integer reply: The number of fields that were added.'
    assert response_list[3].decode() == "bar"


@pytest.mark.asyncio
@pytest.mark.snapshot(variants={"": aioredis_version >= (2, 0), "13": aioredis_version < (2, 0)})
async def test_two_traced_pipelines(redis_client):

    with tracer.trace("web-request", service="test"):
        if aioredis_version >= (2, 0):
            p1 = await redis_client.pipeline(transaction=False)
            p2 = await redis_client.pipeline(transaction=False)
            await p1.set("blah", "boo")
            await p2.set("foo", "bar")
            await p1.get("blah")
            await p2.get("foo")
        else:
            p1 = redis_client.pipeline()
            p2 = redis_client.pipeline()
            p1.set("blah", "boo")
            p2.set("foo", "bar")
            p1.get("blah")
            p2.get("foo")

        response_list1 = await p1.execute()
        response_list2 = await p2.execute()

    assert response_list1[0] is True  # response from redis.set is OK if successfully pushed
    assert response_list2[0] is True
    assert (
        response_list1[1].decode() == "boo"
    )  # response from hset is 'Integer reply: The number of fields that were added.'
    assert response_list2[1].decode() == "bar"
