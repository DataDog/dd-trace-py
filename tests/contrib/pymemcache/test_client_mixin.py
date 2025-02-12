# 3p
import pymemcache
import pytest

from ddtrace.contrib.internal.pymemcache.patch import patch
from ddtrace.contrib.internal.pymemcache.patch import unpatch
from ddtrace.ext import memcached as memcachedx
from ddtrace.ext import net

# project
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import override_config

from .utils import MockSocket


_Client = pymemcache.client.base.Client

TEST_HOST = "localhost"
TEST_PORT = 117711

PYMEMCACHE_VERSION = tuple(int(_) for _ in pymemcache.__version__.split("."))


# Manually configure pymemcached to collect command
@pytest.fixture(scope="module", autouse=True)
def command_enabled():
    with override_config("pymemcache", dict(command_enabled=True)):
        yield


class PymemcacheClientTestCaseMixin(TracerTestCase):
    """Tests for a patched pymemcache.client.base.Client."""

    def get_spans(self):
        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()
        return spans

    def check_spans(self, num_expected, resources_expected, queries_expected):
        """A helper for validating basic span information."""
        spans = self.get_spans()
        self.assertEqual(num_expected, len(spans))

        for span, resource, query in zip(spans, resources_expected, queries_expected):
            self.assert_is_measured(span)
            self.assertEqual(span.get_tag(net.TARGET_HOST), TEST_HOST)
            self.assertEqual(span.get_metric("network.destination.port"), TEST_PORT)
            self.assertEqual(span.name, memcachedx.CMD)
            self.assertEqual(span.span_type, "cache")
            self.assertEqual(span.service, memcachedx.SERVICE)
            self.assertEqual(span.get_tag(memcachedx.QUERY), query)
            self.assertEqual(span.resource, resource)
            self.assertEqual(span.get_tag("component"), "pymemcache")
            self.assertEqual(span.get_tag("span.kind"), "client")
            self.assertEqual(span.get_tag("db.system"), "memcached")

        return spans

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()

    def make_client(self, mock_socket_values, **kwargs):
        tracer = DummyTracer()
        Pin._override(pymemcache, tracer=tracer)
        self.client = pymemcache.client.base.Client((TEST_HOST, TEST_PORT), **kwargs)
        self.client.sock = MockSocket(list(mock_socket_values))
        return self.client

    def test_set_success(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.set(b"key", b"value", noreply=False)
        assert result is True

        self.check_spans(1, ["set"], ["set key"])

    def test_get_many_none_found(self):
        client = self.make_client([b"END\r\n"])
        result = client.get_many([b"key1", b"key2"])
        assert result == {}

        self.check_spans(1, ["get_many"], ["get_many key1 key2"])

    def test_get_multi_none_found(self):
        client = self.make_client([b"END\r\n"])
        result = client.get_multi([b"key1", b"key2"])
        assert result == {}

        self.check_spans(1, ["get_many"], ["get_many key1 key2"])

    def test_get_many_rowcount(self):
        client = self.make_client(
            [
                b"VALUE key 0 5\r\nvalue\r\nVALUE key2 5 6\r\nvalue2\r\nEND\r\n",
                b"VALUE key 0 5\r\nvalue\r\nEND\r\n",
                b"END\r\n",
            ]
        )

        result = client.get_many([b"key", b"key2"])
        assert result == {b"key": b"value", b"key2": b"value2"}
        result = client.get_many([b"key", b"key3"])
        assert result == {b"key": b"value"}
        result = client.get_many([b"key3", b"key4"])
        assert result == {}

        spans = self.check_spans(
            3,
            ["get_many", "get_many", "get_many"],
            ["get_many key key2", "get_many key key3", "get_many key3 key4"],
        )

        get_many_2_keys = spans[0]
        get_many_1_keys = spans[1]
        get_many_0_keys = spans[2]

        assert get_many_2_keys.resource == "get_many"
        assert get_many_2_keys.get_metric("db.row_count") == 2

        assert get_many_1_keys.resource == "get_many"
        assert get_many_1_keys.get_metric("db.row_count") == 1

        assert get_many_0_keys.resource == "get_many"
        assert get_many_0_keys.get_metric("db.row_count") == 0

    def test_gets_many_rowcount(self):
        client = self.make_client(
            [
                b"VALUE key 0 5 10\r\nvalue\r\nVALUE key2 5 6 11\r\nvalue2\r\nEND\r\n",
                b"VALUE key 0 5 10\r\nvalue\r\nEND\r\n",
                b"END\r\n",
            ]
        )

        result = client.gets_many([b"key", b"key2"])
        assert result == {b"key": (b"value", b"10"), b"key2": (b"value2", b"11")}
        result = client.gets_many([b"key", b"key3"])
        assert result == {b"key": (b"value", b"10")}
        result = client.gets_many([b"key3", b"key4"])
        assert result == {}

        spans = self.check_spans(
            3,
            ["gets_many", "gets_many", "gets_many"],
            ["gets_many key key2", "gets_many key key3", "gets_many key3 key4"],
        )

        get_many_2_keys = spans[0]
        get_many_1_keys = spans[1]
        get_many_0_keys = spans[2]

        assert get_many_2_keys.resource == "gets_many"
        assert get_many_2_keys.get_metric("db.row_count") == 2

        assert get_many_1_keys.resource == "gets_many"
        assert get_many_1_keys.get_metric("db.row_count") == 1

        assert get_many_0_keys.resource == "gets_many"
        assert get_many_0_keys.get_metric("db.row_count") == 0

    def test_get_multi_rowcount(self):
        client = self.make_client(
            [
                b"VALUE key 0 5\r\nvalue\r\nVALUE key2 5 6\r\nvalue2\r\nEND\r\n",
                b"VALUE key 0 5\r\nvalue\r\nEND\r\n",
                b"END\r\n",
            ]
        )

        result = client.get_multi([b"key", b"key2"])
        assert result == {b"key": b"value", b"key2": b"value2"}
        result = client.get_multi([b"key", b"key3"])
        assert result == {b"key": b"value"}
        result = client.get_multi([b"key3", b"key4"])
        assert result == {}

        spans = self.check_spans(
            3,
            ["get_many", "get_many", "get_many"],
            ["get_many key key2", "get_many key key3", "get_many key3 key4"],
        )

        get_many_2_keys = spans[0]
        get_many_1_keys = spans[1]
        get_many_0_keys = spans[2]

        assert get_many_2_keys.resource == "get_many"
        assert get_many_2_keys.get_metric("db.row_count") == 2

        assert get_many_1_keys.resource == "get_many"
        assert get_many_1_keys.get_metric("db.row_count") == 1

        assert get_many_0_keys.resource == "get_many"
        assert get_many_0_keys.get_metric("db.row_count") == 0

    def test_delete_not_found(self):
        client = self.make_client([b"NOT_FOUND\r\n"])
        result = client.delete(b"key", noreply=False)
        assert result is False

        self.check_spans(1, ["delete"], ["delete key"])

    def test_incr_found(self):
        client = self.make_client([b"STORED\r\n", b"1\r\n"])
        client.set(b"key", 0, noreply=False)
        result = client.incr(b"key", 1, noreply=False)
        assert result == 1

        self.check_spans(2, ["set", "incr"], ["set key", "incr key"])

    def test_get_found(self):
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        result = client.set(b"key", b"value", noreply=False)
        result = client.get(b"key")
        assert result == b"value"

        self.check_spans(2, ["set", "get"], ["set key", "get key"])

    def test_decr_found(self):
        client = self.make_client([b"STORED\r\n", b"1\r\n"])
        client.set(b"key", 2, noreply=False)
        result = client.decr(b"key", 1, noreply=False)
        assert result == 1

        self.check_spans(2, ["set", "decr"], ["set key", "decr key"])

    def test_add_stored(self):
        client = self.make_client([b"STORED\r", b"\n"])
        result = client.add(b"key", b"value", noreply=False)
        assert result is True

        self.check_spans(1, ["add"], ["add key"])

    def test_delete_many_found(self):
        client = self.make_client([b"STORED\r", b"\n", b"DELETED\r\n"])
        result = client.add(b"key", b"value", noreply=False)
        result = client.delete_many([b"key"], noreply=False)
        assert result is True

        self.check_spans(2, ["add", "delete_many"], ["add key", "delete_many key"])

    def test_set_many_success(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.set_many({b"key": b"value"}, noreply=False)

        resource = "set_many"
        query = "set_many key"
        if PYMEMCACHE_VERSION[0] == 1:
            assert result is True
        elif PYMEMCACHE_VERSION < (3, 4, 4):
            assert result == []
            if isinstance(client, pymemcache.client.hash.HashClient):
                resource = "set"
                query = "set key"
        else:
            assert result == []

        self.check_spans(1, [resource], [query])

    def test_set_multi_success(self):
        # Should just map to set_many
        client = self.make_client([b"STORED\r\n"])
        result = client.set_multi({b"key": b"value"}, noreply=False)

        resource = "set_many"
        query = "set_many key"
        if PYMEMCACHE_VERSION[0] == 1:
            assert result is True
        elif PYMEMCACHE_VERSION < (3, 4, 4):
            assert result == []
            if isinstance(client, pymemcache.client.hash.HashClient):
                resource = "set"
                query = "set key"
        else:
            assert result == []

        self.check_spans(1, [resource], [query])
