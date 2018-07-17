# 3p
import unittest
import wrapt
import pymemcache

# project
from ddtrace import Pin
from ddtrace.contrib.pymemcache.patch import patch, unpatch
from .utils import MockSocket, _str, get_spans, assert_spans

from tests.test_tracer import get_dummy_tracer


_Client = pymemcache.client.base.Client


class PymemcacheClientTestCase(unittest.TestCase):
    """ Tests for pymemcache.client.base.Client. """

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
        assert spans[0].resource == "set"
        assert spans[1].resource == "get"

    def test_append_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.append(b"key", b"value", noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        assert spans[0].resource == "append"

    def test_prepend_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.prepend(b"key", b"value", noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        assert spans[0].resource == "prepend"

    def test_cas_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.cas(b"key", b"value", b"cas", noreply=False)
        assert result is True

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        assert spans[0].resource == "cas"

    def test_cas_exists(self):
        client = self.make_client([b"EXISTS\r\n"])
        result = client.cas(b"key", b"value", b"cas", noreply=False)
        assert result is False

        spans = get_spans(client)
        self.assertEqual(len(spans), 1)
        assert_spans(spans)
        assert spans[0].resource == "cas"


def _get_test_client_tracer(service="test-memcached"):
    from tests.test_tracer import get_dummy_tracer

    tracer = get_dummy_tracer()
    pin = Pin(service=service, tracer=tracer)
    from ddtrace.contrib.pymemcache.trace import WrappedClient

    client = WrappedClient(("localhost", 11211))
    pin.onto(client)
    return client, tracer
