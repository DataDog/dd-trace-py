# 3p
import pymemcache
from pymemcache.client.base import Client
from pymemcache.exceptions import MemcacheClientError
from pymemcache.exceptions import MemcacheIllegalInputError
from pymemcache.exceptions import MemcacheServerError
from pymemcache.exceptions import MemcacheUnknownCommandError
from pymemcache.exceptions import MemcacheUnknownError
import pytest
import wrapt

from ddtrace.contrib.internal.pymemcache.client import WrappedClient
from ddtrace.contrib.internal.pymemcache.patch import patch
from ddtrace.contrib.internal.pymemcache.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME

# project
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import override_config

from .test_client_mixin import PYMEMCACHE_VERSION
from .test_client_mixin import TEST_HOST
from .test_client_mixin import TEST_PORT
from .test_client_mixin import PymemcacheClientTestCaseMixin
from .utils import MockSocket
from .utils import _str


_Client = pymemcache.client.base.Client


# Manually configure pymemcached to collect command
@pytest.fixture(scope="module", autouse=True)
def command_enabled():
    with override_config("pymemcache", dict(command_enabled=True)):
        yield


class PymemcacheClientTestCase(PymemcacheClientTestCaseMixin):
    """Tests for a patched pymemcache.client.base.Client."""

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

        self.check_spans(2, ["set", "get"], ["set key", "get key"])

    def test_get_rowcount(self):
        client = self.make_client([b"VALUE key 0 5\r\nvalue\r\nEND\r\n", b"END\r\n"])

        result = client.get(b"key")
        assert _str(result) == "value"
        result = client.get(b"missing_key")
        assert result is None

        spans = self.check_spans(2, ["get", "get"], ["get key", "get missing_key"])

        get_key_exists_span = spans[0]
        get_key_missing_span = spans[1]

        assert get_key_exists_span.resource == "get"
        assert get_key_exists_span.get_metric("db.row_count") == 1

        assert get_key_missing_span.resource == "get"
        assert get_key_missing_span.get_metric("db.row_count") == 0

    def test_append_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.append(b"key", b"value", noreply=False)
        assert result is True

        self.check_spans(1, ["append"], ["append key"])

    def test_prepend_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.prepend(b"key", b"value", noreply=False)
        assert result is True

        self.check_spans(1, ["prepend"], ["prepend key"])

    def test_cas_stored(self):
        client = self.make_client([b"STORED\r\n"])
        result = client.cas(b"key", b"value", b"0", noreply=False)
        assert result is True

        self.check_spans(1, ["cas"], ["cas key"])

    def test_cas_exists(self):
        client = self.make_client([b"EXISTS\r\n"])
        result = client.cas(b"key", b"value", b"0", noreply=False)
        assert result is False

        self.check_spans(1, ["cas"], ["cas key"])

    def test_cas_not_found(self):
        client = self.make_client([b"NOT_FOUND\r\n"])
        result = client.cas(b"key", b"value", b"0", noreply=False)
        assert result is None

        self.check_spans(1, ["cas"], ["cas key"])

    def test_delete_exception(self):
        client = self.make_client([Exception("fail")])

        def _delete():
            client.delete(b"key", noreply=False)

        pytest.raises(Exception, _delete)

        spans = self.check_spans(1, ["delete"], ["delete key"])
        self.assertEqual(spans[0].error, 1)

    def test_flush_all(self):
        client = self.make_client([b"OK\r\n"])
        result = client.flush_all(noreply=False)
        assert result is True

        self.check_spans(1, ["flush_all"], ["flush_all"])

    def test_incr_exception(self):
        client = self.make_client([Exception("fail")])

        def _incr():
            client.incr(b"key", 1)

        pytest.raises(Exception, _incr)

        spans = self.check_spans(1, ["incr"], ["incr key"])
        self.assertEqual(spans[0].error, 1)

    def test_get_error(self):
        client = self.make_client([b"ERROR\r\n"])

        def _get():
            client.get(b"key")

        pytest.raises(MemcacheUnknownCommandError, _get)

        spans = self.check_spans(1, ["get"], ["get key"])
        self.assertEqual(spans[0].error, 1)

    def test_get_unknown_error(self):
        client = self.make_client([b"foobarbaz\r\n"])

        def _get():
            client.get(b"key")

        pytest.raises(MemcacheUnknownError, _get)

        self.check_spans(1, ["get"], ["get key"])

    def test_gets_found(self):
        client = self.make_client([b"VALUE key 0 5 10\r\nvalue\r\nEND\r\n"])
        result = client.gets(b"key")
        assert result == (b"value", b"10")

        self.check_spans(1, ["gets"], ["gets key"])

    def test_gets_rowcount(self):
        client = self.make_client([b"VALUE key 0 5 10\r\nvalue\r\nEND\r\n", b"END\r\n"])
        result = client.gets(b"key")
        assert result == (b"value", b"10")
        result = client.gets(b"missing_key")
        assert result == (None, None)

        spans = self.check_spans(2, ["gets", "gets"], ["gets key", "gets missing_key"])

        gets_key_exists_span = spans[0]
        gets_key_missing_span = spans[1]

        assert gets_key_exists_span.resource == "gets"
        assert gets_key_exists_span.get_metric("db.row_count") == 1

        assert gets_key_missing_span.resource == "gets"
        assert gets_key_missing_span.get_metric("db.row_count") == 0

    def test_touch_not_found(self):
        client = self.make_client([b"NOT_FOUND\r\n"])
        result = client.touch(b"key", noreply=False)
        assert result is False

        self.check_spans(1, ["touch"], ["touch key"])

    def test_set_client_error(self):
        client = self.make_client([b"CLIENT_ERROR some message\r\n"])

        def _set():
            client.set("key", "value", noreply=False)

        pytest.raises(MemcacheClientError, _set)

        spans = self.check_spans(1, ["set"], ["set key"])
        self.assertEqual(spans[0].error, 1)

    def test_set_server_error(self):
        client = self.make_client([b"SERVER_ERROR some message\r\n"])

        def _set():
            client.set(b"key", b"value", noreply=False)

        pytest.raises(MemcacheServerError, _set)

        spans = self.check_spans(1, ["set"], ["set key"])
        self.assertEqual(spans[0].error, 1)

    def test_set_key_with_space(self):
        client = self.make_client([b""])

        def _set():
            client.set(b"key has space", b"value", noreply=False)

        pytest.raises(MemcacheIllegalInputError, _set)

        spans = self.check_spans(1, ["set"], ["set key has space"])
        self.assertEqual(spans[0].error, 1)

    def test_quit(self):
        client = self.make_client([])
        result = client.quit()
        assert result is None

        self.check_spans(1, ["quit"], ["quit"])

    def test_replace_not_stored(self):
        client = self.make_client([b"NOT_STORED\r\n"])
        result = client.replace(b"key", b"value", noreply=False)
        assert result is False

        self.check_spans(1, ["replace"], ["replace key"])

    def test_version_success(self):
        client = self.make_client([b"VERSION 1.2.3\r\n"], default_noreply=False)
        result = client.version()
        assert result == b"1.2.3"

        self.check_spans(1, ["version"], ["version"])

    def test_stats(self):
        client = self.make_client([b"STAT fake_stats 1\r\n", b"END\r\n"])
        result = client.stats()
        if PYMEMCACHE_VERSION >= (3, 4, 0):
            assert client.sock.send_bufs == [b"stats\r\n"]
        else:
            assert client.sock.send_bufs == [b"stats \r\n"]
        assert result == {b"fake_stats": 1}

        self.check_spans(1, ["stats"], ["stats"])

    def test_service_name_override(self):
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        Pin._override(client, service="testsvcname")
        client.set(b"key", b"value", noreply=False)
        result = client.get(b"key")
        assert _str(result) == "value"

        spans = self.get_spans()
        self.assertEqual(spans[0].service, "testsvcname")
        self.assertEqual(spans[1].service, "testsvcname")


class PymemcacheHashClientTestCase(PymemcacheClientTestCaseMixin):
    """Tests for a patched pymemcache.client.hash.HashClient."""

    def make_client(self, mock_socket_values, **kwargs):
        from pymemcache.client.hash import HashClient

        tracer = DummyTracer()
        Pin._override(pymemcache, tracer=tracer)
        self.client = HashClient([(TEST_HOST, TEST_PORT)], **kwargs)

        class _MockClient(Client):
            def _connect(self):
                self.sock = MockSocket(list(mock_socket_values))

        for inner_client in self.client.clients.values():
            inner_client.client_class = _MockClient
            inner_client.sock = MockSocket(list(mock_socket_values))

        return self.client

    def test_patched_hash_client(self):
        client = self.make_client([b"STORED\r\n"])
        if PYMEMCACHE_VERSION >= (3, 2, 0):
            assert client.client_class == WrappedClient
        assert len(client.clients)
        for _c in client.clients.values():
            assert isinstance(_c, wrapt.ObjectProxy)

    def test_delete_many_found(self):
        """
        delete_many internally calls client.delete so we should expect to get
        delete for our span resource.

        for base.Clients self.delete() is called which by-passes our tracing
        on delete()
        """
        client = self.make_client([b"STORED\r", b"\n", b"DELETED\r\n"])
        result = client.add(b"key", b"value", noreply=False)
        result = client.delete_many([b"key"], noreply=False)
        assert result is True

        self.check_spans(2, ["add", "delete"], ["add key", "delete key"])

    def test_service_name_override_hashclient(self):
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        assert len(client.clients) == 1
        for _c in client.clients.values():
            Pin._override(_c, service="testsvcname")
        client.set(b"key", b"value", noreply=False)
        result = client.get(b"key")
        assert _str(result) == "value"

        spans = self.get_spans()
        assert len(spans) == 2
        self.assertEqual(spans[0].service, "testsvcname")
        self.assertEqual(spans[1].service, "testsvcname")

    def test_service_name_override_hashclient_pooling(self):
        client = self.make_client([b""], use_pooling=True)
        Pin._override(client, service="testsvcname")
        client.set(b"key", b"value")
        assert len(client.clients) == 1
        spans = self.get_spans()
        assert len(spans) == 1
        self.assertEqual(spans[0].service, "testsvcname")


class PymemcacheClientConfiguration(TracerTestCase):
    """Ensure that pymemache can be configured properly."""

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

    def test_same_tracer(self):
        """Ensure same tracer reference is used by the pin on pymemache and
        Clients.
        """
        client = pymemcache.client.base.Client((TEST_HOST, TEST_PORT))
        self.assertEqual(Pin.get_from(client).tracer, Pin.get_from(pymemcache).tracer)

    def test_override_parent_pin(self):
        """Test that the service set on `pymemcache` is used for Clients."""
        Pin._override(pymemcache, service="mysvc")
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        client.set(b"key", b"value", noreply=False)

        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()

        self.assertEqual(spans[0].service, "mysvc")

    def test_override_client_pin(self):
        """Test that the service set on `pymemcache` is used for Clients."""
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        Pin._override(client, service="mysvc2")

        client.set(b"key", b"value", noreply=False)

        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()

        self.assertEqual(spans[0].service, "mysvc2")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_default(self):
        """
        In the default naming schema (v0) -
        When a user specifies a service for the app
            The pymemcache integration **should not** use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        client.set(b"key", b"value", noreply=False)

        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()

        assert spans[0].service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        """
        In the v0 naming schema -
        When a user specifies a service for the app
            The pymemcache integration **should not** use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        client.set(b"key", b"value", noreply=False)

        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()

        assert spans[0].service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        """
        In the v1 naming schema -
        When a user specifies a service for the app
            The pymemcache integration **should** use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        client.set(b"key", b"value", noreply=False)

        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()

        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1(self):
        """
        In the v1 naming schema -
        When a user specifies a service for the app
            The pymemcache integration **should** use it.
        """
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        client.set(b"key", b"value", noreply=False)

        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()

        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_ATTRIBUTE_SCHEMA="v0"))
    def test_operation_name_v0(self):
        """
        v0 schema: The operation name is memcached.command
        """
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        client.set(b"key", b"value", noreply=False)

        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()

        assert spans[0].name == "memcached.command"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_ATTRIBUTE_SCHEMA="v1"))
    def test_operation_name_v1(self):
        """
        v1 schema: The operation name is memcached.command
        """
        client = self.make_client([b"STORED\r\n", b"VALUE key 0 5\r\nvalue\r\nEND\r\n"])
        client.set(b"key", b"value", noreply=False)

        pin = Pin.get_from(pymemcache)
        tracer = pin.tracer
        spans = tracer.pop()

        assert spans[0].name == "memcached.command"
