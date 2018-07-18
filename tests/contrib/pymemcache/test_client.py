# 3p
import unittest
import wrapt
from nose.tools import assert_raises
import pymemcache
from pymemcache.exceptions import (
    MemcacheClientError,
    MemcacheServerError,
    MemcacheUnknownCommandError,
    MemcacheUnknownError,
    MemcacheIllegalInputError,
)

# project
from ddtrace import Pin
from ddtrace.contrib.pymemcache.patch import patch, unpatch
from .utils import MockSocket, _str, get_spans, assert_spans

from tests.test_tracer import get_dummy_tracer


_Client = pymemcache.client.base.Client


class PymemcacheClientTestCase(unittest.TestCase):
    """ Tests for a patched pymemcache.client.base.Client. """

    def make_client(self, mock_socket_values, **kwargs):
        client = pymemcache.client.base.Client(None, **kwargs)
        client.sock = MockSocket(list(mock_socket_values))
        tracer = get_dummy_tracer()
        Pin.override(client, tracer=tracer)
        return client

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()

    def test_patch(self):
        assert issubclass(pymemcache.client.base.Client, wrapt.ObjectProxy)
        client = self.make_client([])
        self.assertIsInstance(client, wrapt.ObjectProxy)

    def test_unpatch(self):
        unpatch()
        from pymemcache.client.base import Client

        self.assertEqual(Client, _Client)

    def test_set_get(self):
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        client.set(b"key", b"value", noreply=False)
        result = client.get(b"key")
        assert _str(result) == "value"

        spans = get_spans(client)
        self.assertEqual(len(spans), 2)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "set")
        self.assertEqual(spans[1].resource, "get")

    def test_append_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.append(b"key", b"value", noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "append")

    def test_prepend_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.prepend(b"key", b"value", noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "prepend")

    def test_cas_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.cas(b"key", b"value", b"cas", noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "cas")

    def test_cas_exists(self):
        client = self.make_client([b"EXISTS\r\n"])
        result = client.cas(b"key", b"value", b"cas", noreply=False)
        assert result is False

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "cas")

    def test_cas_not_found(self):
        client = self.make_client([b"NOT_FOUND\r\n"])
        result = client.cas(b"key", b"value", b"cas", noreply=False)
        assert result is None

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "cas")

    def test_cr_nl_boundaries(self):
        client = self.make_client(
            [
                b"VALUE key1 0 6\r",
                b"\nvalue1\r\n" b"VALUE key2 0 6\r\n",
                b"value2\r\n" b"END\r\n",
            ]
        )
        result = client.get_many([b"key1", b"key2"])
        assert result == {b"key1": b"value1", b"key2": b"value2"}

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "get_many")

    def test_delete_exception(self):
        client = self.make_client([Exception("fail")])

        def _delete():
            client.delete(b"key", noreply=False)

        assert_raises(Exception, _delete)

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "delete")
        self.assertEqual(spans[0].error, 1)

    def test_flush_all(self):
        client = self.make_client([b"OK\r\n"])
        result = client.flush_all(noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "flush_all")

    def test_incr_found(self):
        client = self.make_client([b"STORED\r\n", b"1\r\n"])
        client.set(b"key", 0, noreply=False)
        result = client.incr(b"key", 1, noreply=False)
        assert result == 1

        spans = get_spans(client)
        self.assertEqual(len(spans), 2)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "set")
        self.assertEqual(spans[1].resource, "incr")

    def test_incr_exception(self):
        client = self.make_client([Exception("fail")])

        def _incr():
            client.incr(b"key", 1)

        assert_raises(Exception, _incr)

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "incr")

    def test_get_error(self):
        client = self.make_client([b"ERROR\r\n"])

        def _get():
            client.get(b"key")

        assert_raises(MemcacheUnknownCommandError, _get)

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "get")
        self.assertEqual(spans[0].error, 1)

    def test_get_unknown_error(self):
        client = self.make_client([b"foobarbaz\r\n"])

        def _get():
            client.get(b"key")

        assert_raises(MemcacheUnknownError, _get)

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "get")

    def test_gets_found(self):
        client = self.make_client([b"VALUE key 0 5 10\r\nvalue\r\nEND\r\n"])
        result = client.gets(b"key")
        assert result == (b"value", b"10")

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "gets")

    def test_gets_not_found_defaults(self):
        client = self.make_client([b"END\r\n"])
        result = client.gets(b"key", default="foo", cas_default="bar")
        assert result == ("foo", "bar")

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "gets")

    def test_gets_found(self):
        client = self.make_client([b"VALUE key 0 5 10\r\nvalue\r\nEND\r\n"])
        result = client.gets(b"key")
        assert result == (b"value", b"10")

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "gets")

    def test_touch_not_found(self):
        client = self.make_client([b"NOT_FOUND\r\n"])
        result = client.touch(b"key", noreply=False)
        assert result is False

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "touch")

    def test_set_client_error(self):
        client = self.make_client([b"CLIENT_ERROR some message\r\n"])

        def _set():
            client.set("key", "value", noreply=False)

        assert_raises(MemcacheClientError, _set)

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].error, 1)

    def test_set_server_error(self):
        client = self.make_client([b"SERVER_ERROR some message\r\n"])

        def _set():
            client.set(b"key", b"value", noreply=False)

        assert_raises(MemcacheServerError, _set)

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].error, 1)

    def test_set_key_with_space(self):
        client = self.make_client([b""])

        def _set():
            client.set(b"key has space", b"value", noreply=False)

        assert_raises(MemcacheIllegalInputError, _set)

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].error, 1)

    def test_quit(self):
        client = self.make_client([])
        result = client.quit()
        assert result is None

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "quit")

    def test_replace_not_stored(self):
        client = self.make_client([b"NOT_STORED\r\n"])
        result = client.replace(b"key", b"value", noreply=False)
        assert result is False

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "replace")

    def test_version_success(self):
        client = self.make_client([b"VERSION 1.2.3\r\n"], default_noreply=False)
        result = client.version()
        assert result == b"1.2.3"

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "version")

    def test_stats(self):
        client = self.make_client([b"STAT fake_stats 1\r\n", b"END\r\n"])
        result = client.stats()
        assert client.sock.send_bufs == [b"stats \r\n"]
        assert result == {b"fake_stats": 1}

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "stats")

    def test_decr_found(self):
        client = self.make_client([b"STORED\r\n", b"1\r\n"])
        client.set(b"key", 2, noreply=False)
        result = client.decr(b"key", 1, noreply=False)
        assert result == 1

        spans = get_spans(client)
        self.assertEqual(len(spans), 2)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "set")
        self.assertEqual(spans[1].resource, "decr")

    def test_add_stored(self):
        client = self.make_client([b"STORED\r", b"\n"])
        result = client.add(b"key", b"value", noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "add")

    def test_delete_many_found(self):
        client = self.make_client([b"STORED\r", b"\n", b"DELETED\r\n"])
        result = client.add(b"key", b"value", noreply=False)
        result = client.delete_many([b"key"], noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 2)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "add")
        self.assertEqual(spans[1].resource, "delete_many")

    def test_set_many_success(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.set_many({b"key": b"value"}, noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        self.assertEqual(spans[0].resource, "set_many")
