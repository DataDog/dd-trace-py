import asyncio

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
    r = await get_redis_instance(max_connections=10)  # default values
    yield r


@pytest.mark.asyncio
@pytest.fixture
async def single_pool_redis_client():
    r = await get_redis_instance(max_connections=1)
    yield r


def get_redis_instance(max_connections: int):
    if aioredis_version >= (2, 0):
        return aioredis.from_url("redis://127.0.0.1:%s" % REDIS_CONFIG["port"], max_connections=max_connections)
    return aioredis.create_redis_pool(("127.0.0.1", REDIS_CONFIG["port"]), maxsize=max_connections)


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
        assert isinstance(aioredis.commands.Redis.execute, ObjectProxy)
        unpatch()
        assert not isinstance(aioredis.Redis.execute, ObjectProxy)
        assert not isinstance(aioredis.commands.Redis.execute, ObjectProxy)


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_basic_request(redis_client):
    val = await redis_client.get("cheese")
    assert val is None


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_unicode_request(redis_client):
    val = await redis_client.get(u"😐")
    assert val is None


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_decoding_non_utf8_args(redis_client):
    await redis_client.set(b"\x80foo", b"\x80abc")
    val = await redis_client.get(b"\x80foo")
    assert val == b"\x80abc"


@pytest.mark.asyncio
@pytest.mark.snapshot(variants={"": aioredis_version >= (2, 0), "13": aioredis_version < (2, 0)})
async def test_decoding_non_utf8_pipeline_args(redis_client):
    if aioredis_version >= (2, 0):
        p = await redis_client.pipeline(transaction=False)
        await p.set(b"\x80blah", "boo")
        await p.set("foo", b"\x80abc")
        await p.get(b"\x80blah")
        await p.get("foo")
    else:
        p = redis_client.pipeline()
        p.set(b"\x80blah", "boo")
        p.set("foo", b"\x80abc")
        p.get(b"\x80blah")
        p.get("foo")

    response_list = await p.execute()
    assert response_list[0] is True  # response from redis.set is OK if successfully pushed
    assert response_list[1] is True
    assert response_list[2].decode() == "boo"
    assert response_list[3] == b"\x80abc"


@pytest.mark.skipif(aioredis_version > (2, 0), reason="only affects aioredis < 2.0")
@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_closed_connection_pool(single_pool_redis_client):
    """
    Make sure it doesn't raise error when no free connections are available.
    After aioredis 2.0 it raises Too many connections error when it happens.
    """

    async def execute_task():
        """Execute call in the background as a task."""
        return single_pool_redis_client.get("cheese")

    # start running the task after blocking the pool
    with (await single_pool_redis_client):
        task = [asyncio.ensure_future(execute_task())]
    # Pool is released, make sure we wait for the task to finish
    await asyncio.gather(*task, return_exceptions=True)


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


@pytest.mark.skipif(aioredis_version < (2, 0), reason="only supported in aioredis >= 2.0")
@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_pipeline_traced_context_manager_transaction(redis_client):
    """
    Regression test for: https://github.com/DataDog/dd-trace-py/issues/3106

    https://aioredis.readthedocs.io/en/latest/migration/#pipelines-and-transactions-multiexec

    Example::

        async def main():
            redis = await aioredis.from_url("redis://localhost")
            async with redis.pipeline(transaction=True) as pipe:
                ok1, ok2 = await (pipe.set("key1", "value1").set("key2", "value2").execute())
            assert ok1
            assert ok2
    """

    async with redis_client.pipeline(transaction=True) as p:
        set_1, set_2, get_1, get_2 = await (p.set("blah", "boo").set("foo", "bar").get("blah").get("foo").execute())

    # response from redis.set is OK if successfully pushed
    assert set_1 is True
    assert set_2 is True
    assert get_1.decode() == "boo"
    assert get_2.decode() == "bar"


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


@pytest.mark.asyncio
@pytest.mark.snapshot(variants={"": aioredis_version >= (2, 0), "13": aioredis_version < (2, 0)})
async def test_parenting(redis_client):
    with tracer.trace("web-request", service="test"):
        await redis_client.set("blah", "boo")
        await redis_client.get("blah")
