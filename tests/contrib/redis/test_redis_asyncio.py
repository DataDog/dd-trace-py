import typing

import pytest
import redis
import redis.asyncio

from ddtrace import Pin
from ddtrace import tracer
from ddtrace.contrib.redis.patch import patch
from ddtrace.contrib.redis.patch import unpatch
from ddtrace.vendor.wrapt import ObjectProxy
from tests.utils import override_config

from ..config import REDIS_CONFIG


def get_redis_instance(max_connections: int, client_name: typing.Optional[str] = None):
    return redis.asyncio.from_url(
        "redis://127.0.0.1:%s" % REDIS_CONFIG["port"], max_connections=max_connections, client_name=client_name
    )


@pytest.mark.asyncio
@pytest.fixture
def redis_client():
    r = get_redis_instance(max_connections=10)  # default values
    yield r


@pytest.mark.asyncio
@pytest.fixture
def single_pool_redis_client():
    r = get_redis_instance(max_connections=1)
    yield r


@pytest.mark.asyncio
@pytest.fixture(autouse=True)
async def traced_redis(redis_client):
    await redis_client.flushall()

    patch()
    try:
        yield
    finally:
        unpatch()
    await redis_client.flushall()


def test_patching():
    """
    When patching redis library
        We wrap the correct methods
    When unpatching  redis library
        We unwrap the correct methods
    """
    assert isinstance(redis.asyncio.client.Redis.execute_command, ObjectProxy)
    assert isinstance(redis.asyncio.client.Redis.pipeline, ObjectProxy)
    assert isinstance(redis.asyncio.client.Pipeline.pipeline, ObjectProxy)
    unpatch()
    assert not isinstance(redis.asyncio.client.Redis.execute_command, ObjectProxy)
    assert not isinstance(redis.asyncio.client.Redis.pipeline, ObjectProxy)
    assert not isinstance(redis.asyncio.client.Pipeline.pipeline, ObjectProxy)


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_basic_request(redis_client):
    val = await redis_client.get("cheese")
    assert val is None


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_unicode_request(redis_client):
    val = await redis_client.get("😐")
    assert val is None


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_decoding_non_utf8_args(redis_client):
    await redis_client.set(b"\x80foo", b"\x80abc")
    val = await redis_client.get(b"\x80foo")
    assert val == b"\x80abc"


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_decoding_non_utf8_pipeline_args(redis_client):
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
    with override_config("redis", dict(service_name="myredis")):
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
    Pin.override(redis_client, service="my-redis")
    val = await redis_client.get("cheese")
    assert val is None


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_pipeline_traced(redis_client):
    p = redis_client.pipeline(transaction=False)
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
@pytest.mark.snapshot
async def test_pipeline_traced_context_manager_transaction(redis_client):
    """
    Regression test for: https://github.com/DataDog/dd-trace-py/issues/3106

    Example::

        async def main():
            redis = await redis.from_url("redis://localhost")
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
@pytest.mark.snapshot
async def test_two_traced_pipelines(redis_client):

    with tracer.trace("web-request", service="test"):
        if redis.VERSION >= (2, 0):
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
@pytest.mark.snapshot
async def test_parenting(redis_client):
    with tracer.trace("web-request", service="test"):
        await redis_client.set("blah", "boo")
        await redis_client.get("blah")


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_client_name():
    with tracer.trace("web-request", service="test"):
        redis_client = get_redis_instance(10, client_name="testing-client-name")
        await redis_client.get("blah")
