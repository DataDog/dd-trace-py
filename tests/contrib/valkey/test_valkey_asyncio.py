import asyncio
import typing
from unittest import mock

import pytest
import valkey
import valkey.asyncio
from wrapt import ObjectProxy

from ddtrace import tracer
from ddtrace.contrib.internal.valkey.patch import patch
from ddtrace.contrib.internal.valkey.patch import unpatch
from ddtrace.trace import Pin
from tests.utils import override_config

from ..config import VALKEY_CONFIG


def get_valkey_instance(max_connections: int, client_name: typing.Optional[str] = None):
    return valkey.asyncio.from_url(
        "valkey://127.0.0.1:%s" % VALKEY_CONFIG["port"], max_connections=max_connections, client_name=client_name
    )


@pytest.fixture
def valkey_client():
    r = get_valkey_instance(max_connections=10)  # default values
    yield r


@pytest.fixture
def single_pool_valkey_client():
    r = get_valkey_instance(max_connections=1)
    yield r


@pytest.fixture(autouse=True)
async def traced_valkey(valkey_client):
    await valkey_client.flushall()

    patch()
    try:
        yield
    finally:
        unpatch()
    await valkey_client.flushall()


def test_patching():
    """
    When patching valkey library
        We wrap the correct methods
    When unpatching  valkey library
        We unwrap the correct methods
    """
    assert isinstance(valkey.asyncio.client.Valkey.execute_command, ObjectProxy)
    assert isinstance(valkey.asyncio.client.Valkey.pipeline, ObjectProxy)
    assert isinstance(valkey.asyncio.client.Pipeline.pipeline, ObjectProxy)
    unpatch()
    assert not isinstance(valkey.asyncio.client.Valkey.execute_command, ObjectProxy)
    assert not isinstance(valkey.asyncio.client.Valkey.pipeline, ObjectProxy)
    assert not isinstance(valkey.asyncio.client.Pipeline.pipeline, ObjectProxy)


@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_basic_request(valkey_client):
    val = await valkey_client.get("cheese")
    assert val is None


@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_unicode_request(valkey_client):
    val = await valkey_client.get("😐")
    assert val is None


@pytest.mark.snapshot(wait_for_num_traces=1, ignores=["meta.error.stack"])
async def test_connection_error(valkey_client):
    with mock.patch.object(
        valkey.asyncio.connection.ConnectionPool,
        "get_connection",
        side_effect=valkey.exceptions.ConnectionError("whatever"),
    ):
        with pytest.raises(valkey.exceptions.ConnectionError):
            await valkey_client.get("foo")


@pytest.mark.snapshot(wait_for_num_traces=2)
async def test_decoding_non_utf8_args(valkey_client):
    await valkey_client.set(b"\x80foo", b"\x80abc")
    val = await valkey_client.get(b"\x80foo")
    assert val == b"\x80abc"


@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_decoding_non_utf8_pipeline_args(valkey_client):
    p = valkey_client.pipeline()
    p.set(b"\x80blah", "boo")
    p.set("foo", b"\x80abc")
    p.get(b"\x80blah")
    p.get("foo")

    response_list = await p.execute()
    assert response_list[0] is True  # response from valkey.set is OK if successfully pushed
    assert response_list[1] is True
    assert response_list[2].decode() == "boo"
    assert response_list[3] == b"\x80abc"


@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_long_command(valkey_client):
    length = 1000
    val_list = await valkey_client.mget(*range(length))
    assert len(val_list) == length
    for val in val_list:
        assert val is None


@pytest.mark.snapshot(wait_for_num_traces=3)
async def test_override_service_name(valkey_client):
    with override_config("valkey", dict(service_name="myvalkey")):
        val = await valkey_client.get("cheese")
        assert val is None
        await valkey_client.set("cheese", "my-cheese")
        val = await valkey_client.get("cheese")
        if isinstance(val, bytes):
            val = val.decode()
        assert val == "my-cheese"


@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_pin(valkey_client):
    Pin._override(valkey_client, service="my-valkey")
    val = await valkey_client.get("cheese")
    assert val is None


@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_pipeline_traced(valkey_client):
    p = valkey_client.pipeline(transaction=False)
    p.set("blah", "boo")
    p.set("foo", "bar")
    p.get("blah")
    p.get("foo")

    response_list = await p.execute()
    assert response_list[0] is True  # response from valkey.set is OK if successfully pushed
    assert response_list[1] is True
    assert (
        response_list[2].decode() == "boo"
    )  # response from hset is 'Integer reply: The number of fields that were added.'
    assert response_list[3].decode() == "bar"


@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_pipeline_traced_context_manager_transaction(valkey_client):
    """
    Regression test for: https://github.com/DataDog/dd-trace-py/issues/3106

    Example::

        async def main():
            valkey = await valkey.from_url("valkey://localhost")
            async with valkey.pipeline(transaction=True) as pipe:
                ok1, ok2 = await (pipe.set("key1", "value1").set("key2", "value2").execute())
            assert ok1
            assert ok2
    """

    async with valkey_client.pipeline(transaction=True) as p:
        set_1, set_2, get_1, get_2 = await p.set("blah", "boo").set("foo", "bar").get("blah").get("foo").execute()

    # response from valkey.set is OK if successfully pushed
    assert set_1 is True
    assert set_2 is True
    assert get_1.decode() == "boo"
    assert get_2.decode() == "bar"


@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_two_traced_pipelines(valkey_client):
    with tracer.trace("web-request", service="test"):
        p1 = await valkey_client.pipeline(transaction=False)
        p2 = await valkey_client.pipeline(transaction=False)
        await p1.set("blah", "boo")
        await p2.set("foo", "bar")
        await p1.get("blah")
        await p2.get("foo")

        response_list1 = await p1.execute()
        response_list2 = await p2.execute()

    assert response_list1[0] is True  # response from valkey.set is OK if successfully pushed
    assert response_list2[0] is True
    assert (
        response_list1[1].decode() == "boo"
    )  # response from hset is 'Integer reply: The number of fields that were added.'
    assert response_list2[1].decode() == "bar"


async def test_parenting(valkey_client, snapshot_context):
    with snapshot_context(wait_for_num_traces=1):
        with tracer.trace("web-request", service="test"):
            await valkey_client.set("blah", "boo")
            await valkey_client.get("blah")


async def test_client_name(snapshot_context):
    with snapshot_context(wait_for_num_traces=1):
        with tracer.trace("web-request", service="test"):
            valkey_client = get_valkey_instance(10, client_name="testing-client-name")
            await valkey_client.get("blah")


@pytest.mark.asyncio
async def test_asyncio_task_cancelled(valkey_client):
    with mock.patch.object(
        valkey.asyncio.connection.ConnectionPool, "get_connection", side_effect=asyncio.CancelledError
    ):
        with pytest.raises(asyncio.CancelledError):
            await valkey_client.get("foo")
