# stdlib
import time
from unittest.case import SkipTest

# 3p
import pylibmc

from ddtrace.contrib.internal.pylibmc.client import TracedClient
from ddtrace.contrib.internal.pylibmc.patch import patch
from ddtrace.contrib.internal.pylibmc.patch import unpatch
from ddtrace.ext import memcached

# project
from ddtrace.trace import Pin
from tests.contrib.config import MEMCACHED_CONFIG as cfg
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


class PylibmcCore(object):
    """Core of the test suite for pylibmc

    Shared tests between the patch and TracedClient interface.
    Will be merge back to a single class once the TracedClient is deprecated.
    """

    TEST_SERVICE = memcached.SERVICE

    def get_client(self):
        # Implement me
        pass

    def test_upgrade(self):
        raise SkipTest("upgrade memcached")
        # add tests for touch, cas, gets etc

    def test_append_prepend(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.set("a", "crow")
        client.prepend("a", "holy ")
        client.append("a", "!")

        # FIXME[matt] there is a bug in pylibmc & python 3 (perhaps with just
        # some versions of the libmemcache?) where append/prepend are replaced
        # with get. our traced versions do the right thing, so skipping this
        # test.
        try:
            assert client.get("a") == "holy crow!"
        except AssertionError:
            pass

        end = time.time()
        # verify spans
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
        expected_resources = sorted(["append", "prepend", "get", "set"])
        resources = sorted(s.resource for s in spans)
        assert expected_resources == resources

    def test_incr_decr(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.set("a", 1)
        client.incr("a", 2)
        client.decr("a", 1)
        v = client.get("a")
        assert v == 2
        end = time.time()
        # verify spans
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
        expected_resources = sorted(["get", "set", "incr", "decr"])
        resources = sorted(s.resource for s in spans)
        assert expected_resources == resources

    def test_incr_decr_ot(self):
        """OpenTracing version of test_incr_decr."""
        client, tracer = self.get_client()
        ot_tracer = init_tracer("memcached", tracer)

        start = time.time()
        with ot_tracer.start_active_span("mc_ops"):
            client.set("a", 1)
            client.incr("a", 2)
            client.decr("a", 1)
            v = client.get("a")
            assert v == 2
        end = time.time()

        # verify spans
        spans = tracer.pop()
        ot_span = spans[0]

        assert ot_span.name == "mc_ops"

        for s in spans[1:]:
            assert s.parent_id == ot_span.span_id
            self._verify_cache_span(s, start, end)
        expected_resources = sorted(["get", "set", "incr", "decr"])
        resources = sorted(s.resource for s in spans[1:])
        assert expected_resources == resources

    def test_clone(self):
        # ensure cloned connections are traced as well.
        client, tracer = self.get_client()
        cloned = client.clone()
        start = time.time()
        cloned.get("a")
        end = time.time()
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
        expected_resources = ["get"]
        resources = sorted(s.resource for s in spans)
        assert expected_resources == resources

    def test_get_set_multi(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.set_multi({"a": 1, "b": 2})
        out = client.get_multi(["a", "c"])
        assert out == {"a": 1}
        client.delete_multi(["a", "c"])
        end = time.time()
        # verify
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
        expected_resources = sorted(["get_multi", "set_multi", "delete_multi"])
        resources = sorted(s.resource for s in spans)
        assert expected_resources == resources

    def test_get_rowcount(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.set_multi({"a": 1, "b": 2})
        out = client.get("a")
        assert out == 1
        out = client.get("c")
        assert out is None
        end = time.time()
        # verify
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)

        get_existing_key_span = spans[1]
        get_missing_key_span = spans[2]

        assert get_existing_key_span.resource == "get"
        assert get_existing_key_span.get_metric("db.row_count") == 1
        assert get_missing_key_span.resource == "get"
        assert get_missing_key_span.get_metric("db.row_count") == 0

    def test_add(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.add("a", "first")
        out = client.get("a")
        assert out == "first"
        client.add("a", "second")
        out = client.get("a")
        assert out == "first"
        end = time.time()
        # verify
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)

        assert spans[0].resource == "add"
        assert spans[1].resource == "get"
        assert spans[2].resource == "add"
        assert spans[3].resource == "get"

    def test_get_multi_rowcount(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.set_multi({"a": 1, "b": 2})
        out = client.get_multi(["a", "b"])
        assert out == {"a": 1, "b": 2}
        out = client.get_multi(["a", "c"])
        assert out == {"a": 1}
        out = client.get_multi(["c", "d"])
        assert out == {}
        end = time.time()
        # verify
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)

        get_multi_2_keys_exist_span = spans[1]
        get_multi_1_keys_exist_span = spans[2]
        get_multi_0_keys_exist_span = spans[3]

        assert get_multi_2_keys_exist_span.resource == "get_multi"
        assert get_multi_2_keys_exist_span.get_metric("db.row_count") == 2
        assert get_multi_1_keys_exist_span.resource == "get_multi"
        assert get_multi_1_keys_exist_span.get_metric("db.row_count") == 1
        assert get_multi_0_keys_exist_span.resource == "get_multi"
        assert get_multi_0_keys_exist_span.get_metric("db.row_count") == 0

    def test_get_set_multi_prefix(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.set_multi({"a": 1, "b": 2}, key_prefix="foo")
        out = client.get_multi(["a", "c"], key_prefix="foo")
        assert out == {"a": 1}
        client.delete_multi(["a", "c"], key_prefix="foo")
        end = time.time()
        # verify
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
            assert s.get_tag("memcached.query") == "%s foo" % s.resource
            assert s.get_tag("component") == "pylibmc"
            assert s.get_tag("span.kind") == "client"
        expected_resources = sorted(["get_multi", "set_multi", "delete_multi"])
        resources = sorted(s.resource for s in spans)
        assert expected_resources == resources

    def test_get_set_delete(self):
        client, tracer = self.get_client()
        # test
        k = "cafe"
        v = "val-foo"
        start = time.time()
        client.delete(k)  # just in case
        out = client.get(k)
        assert out is None, out
        client.set(k, v)
        out = client.get(k)
        assert out == v
        end = time.time()
        # verify
        spans = tracer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
            assert s.get_tag("memcached.query") == "%s %s" % (s.resource, k)
            assert s.get_tag("component") == "pylibmc"
            assert s.get_tag("span.kind") == "client"
        expected_resources = sorted(["get", "get", "delete", "set"])
        resources = sorted(s.resource for s in spans)
        assert expected_resources == resources

    def _verify_cache_span(self, s, start, end):
        assert_is_measured(s)
        assert s.start > start
        assert s.start + s.duration < end
        assert s.service == self.TEST_SERVICE
        assert s.span_type == "cache"
        assert s.name == "memcached.cmd"
        assert s.get_tag("out.host") == cfg["host"]
        assert s.get_tag("component") == "pylibmc"
        assert s.get_tag("span.kind") == "client"
        assert s.get_tag("db.system") == "memcached"
        assert s.get_metric("network.destination.port") == cfg["port"]

    def test_disabled(self):
        """
        Ensure client works when the tracer is disabled
        """
        client, tracer = self.get_client()
        try:
            tracer.enabled = False

            client.set("a", "crow")

            spans = self.get_spans()
            assert len(spans) == 0

            client.get("a")

            client.get_multi(["a"])
        finally:
            tracer.enabled = True


class TestPylibmcLegacy(TracerTestCase, PylibmcCore):
    """Test suite for the tracing of pylibmc with the legacy TracedClient interface"""

    TEST_SERVICE = "mc-legacy"

    def get_client(self):
        url = "%s:%s" % (cfg["host"], cfg["port"])
        raw_client = pylibmc.Client([url])
        raw_client.flush_all()

        client = TracedClient(raw_client, tracer=self.tracer, service=self.TEST_SERVICE)
        return client, self.tracer


class TestPylibmcPatchDefault(TracerTestCase, PylibmcCore):
    """Test suite for the tracing of pylibmc with the default lib patching"""

    def setUp(self):
        super(TestPylibmcPatchDefault, self).setUp()
        patch()

    def tearDown(self):
        unpatch()
        super(TestPylibmcPatchDefault, self).tearDown()

    def get_client(self):
        url = "%s:%s" % (cfg["host"], cfg["port"])
        client = pylibmc.Client([url])
        client.flush_all()

        Pin.get_from(client).clone(tracer=self.tracer).onto(client)

        return client, self.tracer


class TestPylibmcPatch(TestPylibmcPatchDefault):
    """Test suite for the tracing of pylibmc with a configured lib patching"""

    TEST_SERVICE = "mc-custom-patch"

    def get_client(self):
        client, tracer = TestPylibmcPatchDefault.get_client(self)

        Pin.get_from(client).clone(service=self.TEST_SERVICE).onto(client)

        return client, tracer

    def test_patch_unpatch(self):
        url = "%s:%s" % (cfg["host"], cfg["port"])

        # Test patch idempotence
        patch()
        patch()

        client = pylibmc.Client([url])
        Pin.get_from(client).clone(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)

        client.set("a", 1)

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        client = pylibmc.Client([url])
        client.set("a", 1)

        spans = self.pop_spans()
        assert not spans, spans

        # Test patch again
        patch()

        client = pylibmc.Client([url])
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.set("a", 1)

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

    def test_client_with_servers_option(self):
        """
        A test to make sure we support using `servers`, ie: with pylibmc.Client(servers=[url])
        """
        url = "%s:%s" % (cfg["host"], cfg["port"])

        patch()
        client = pylibmc.Client(servers=[url])
        assert client.addresses[0] is url
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.set("a", 1)
        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

    def test_client_without_servers_option(self):
        """
        A test to make sure we support the most basic use case of calling pylibmc.Client([url])
        """
        url = "%s:%s" % (cfg["host"], cfg["port"])

        patch()
        client = pylibmc.Client([url])
        assert client.addresses[0] is url
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(client)
        client.set("a", 1)
        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1


class TestPylibmcPatchSchematization(TestPylibmcPatchDefault):
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        """
        v0 schema: When a user specifies a service for the app
            The pylibmc integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        client, tracer = self.get_client()
        client.set("a", "crow")
        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        """
        v1 schema: When a user specifies a service for the app
            The pylibmc integration should use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        client, tracer = self.get_client()
        client.set("a", "crow")
        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_operation_name_v0_schema(self):
        """
        v0 schema: memcached.cmd
        """
        client, tracer = self.get_client()
        client.set("a", "crow")
        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "memcached.cmd"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_operation_name_v1_schema(self):
        """
        v1 schema: memcached.command
        """
        client, tracer = self.get_client()
        client.set("a", "crow")
        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "memcached.command"
