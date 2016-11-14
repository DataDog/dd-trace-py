
# stdlib
import time
from unittest.case import SkipTest

# 3p
import pylibmc
from nose.tools import eq_

# project
from ddtrace import Tracer, Pin
from ddtrace.ext import memcached
from ddtrace.contrib.pylibmc import TracedClient
from ddtrace.contrib.pylibmc.patch import patch, unpatch
from tests.test_tracer import DummyWriter
from tests.contrib.config import MEMCACHED_CONFIG as cfg


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
            eq_(client.get("a"), "holy crow!")
        except AssertionError:
            pass

        end = time.time()
        # verify spans
        spans = tracer.writer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
        expected_resources = sorted(["append", "prepend", "get", "set"])
        resources = sorted(s.resource for s in spans)
        eq_(expected_resources, resources)

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
        spans = tracer.writer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
        expected_resources = sorted(["get", "set", "incr", "decr"])
        resources = sorted(s.resource for s in spans)
        eq_(expected_resources, resources)


    def test_clone(self):
        # ensure cloned connections are traced as well.
        client, tracer = self.get_client()
        cloned = client.clone()
        start = time.time()
        cloned.get("a")
        end = time.time()
        spans = tracer.writer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
        expected_resources = ["get"]
        resources = sorted(s.resource for s in spans)
        eq_(expected_resources, resources)

    def test_get_set_multi(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.set_multi({"a":1, "b":2})
        out = client.get_multi(["a", "c"])
        eq_(out, {"a":1})
        client.delete_multi(["a", "c"])
        end = time.time()
        # verify
        spans = tracer.writer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
        expected_resources = sorted(["get_multi", "set_multi", "delete_multi"])
        resources = sorted(s.resource for s in spans)
        eq_(expected_resources, resources)

    def test_get_set_multi_prefix(self):
        client, tracer = self.get_client()
        # test
        start = time.time()
        client.set_multi({"a":1, "b":2}, key_prefix='foo')
        out = client.get_multi(["a", "c"], key_prefix='foo')
        eq_(out, {"a":1})
        client.delete_multi(["a", "c"], key_prefix='foo')
        end = time.time()
        # verify
        spans = tracer.writer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
            eq_(s.get_tag("memcached.query"), "%s foo" % s.resource,)
        expected_resources = sorted(["get_multi", "set_multi", "delete_multi"])
        resources = sorted(s.resource for s in spans)
        eq_(expected_resources, resources)


    def test_get_set_delete(self):
        client, tracer = self.get_client()
        # test
        k = u'cafe'
        v = "val-foo"
        start = time.time()
        client.delete(k) # just in case
        out = client.get(k)
        assert out is None, out
        client.set(k, v)
        out = client.get(k)
        eq_(out, v)
        end = time.time()
        # verify
        spans = tracer.writer.pop()
        for s in spans:
            self._verify_cache_span(s, start, end)
            eq_(s.get_tag("memcached.query"), "%s %s" % (s.resource, k))
        expected_resources = sorted(["get", "get", "delete", "set"])
        resources = sorted(s.resource for s in spans)
        eq_(expected_resources, resources)


    def _verify_cache_span(self, s, start, end):
        assert s.start > start
        assert s.start + s.duration < end
        eq_(s.service, self.TEST_SERVICE)
        eq_(s.span_type, "cache")
        eq_(s.name, "memcached.cmd")
        eq_(s.get_tag("out.host"), cfg["host"])
        eq_(s.get_tag("out.port"), str(cfg["port"]))



class TestPylibmcLegacy(PylibmcCore):
    """Test suite for the tracing of pylibmc with the legacy TracedClient interface"""

    TEST_SERVICE = 'mc-legacy'

    def get_client(self):
        url = "%s:%s" % (cfg["host"], cfg["port"])
        raw_client = pylibmc.Client([url])
        raw_client.flush_all()

        tracer = Tracer()
        tracer.writer = DummyWriter()

        client = TracedClient(raw_client, tracer=tracer, service=self.TEST_SERVICE)
        return client, tracer


class TestPylibmcPatchDefault(PylibmcCore):
    """Test suite for the tracing of pylibmc with the default lib patching"""

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()

    def get_client(self):
        url = "%s:%s" % (cfg["host"], cfg["port"])
        client = pylibmc.Client([url])
        client.flush_all()

        tracer = Tracer()
        tracer.writer = DummyWriter()
        Pin.get_from(client).tracer = tracer

        return client, tracer

class TestPylibmcPatch(TestPylibmcPatchDefault):
    """Test suite for the tracing of pylibmc with a configured lib patching"""

    TEST_SERVICE = 'mc-custom-patch'

    def get_client(self):
        client, tracer = TestPylibmcPatchDefault.get_client(self)

        Pin.get_from(client).service = self.TEST_SERVICE

        return client, tracer
