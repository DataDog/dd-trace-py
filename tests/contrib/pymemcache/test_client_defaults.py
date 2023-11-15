# 3p
import pymemcache
import pytest

# project
from ddtrace import Pin
from ddtrace.contrib.pymemcache.patch import patch
from ddtrace.contrib.pymemcache.patch import unpatch
from tests.utils import override_config

from .test_client_mixin import TEST_HOST
from .test_client_mixin import TEST_PORT
from .utils import MockSocket


@pytest.fixture()
def client(tracer):
    try:
        patch()
        Pin.override(pymemcache, tracer=tracer)
        with override_config("pymemcache", dict(command_enabled=False)):
            client = pymemcache.client.base.Client((TEST_HOST, TEST_PORT))
            yield client
    finally:
        unpatch()


def test_query_default(client, tracer):
    client.sock = MockSocket([b"STORED\r\n"])
    result = client.set(b"key", b"value", noreply=False)
    assert result is True

    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    assert traces[0][0].get_tag("memcached.query") is None
