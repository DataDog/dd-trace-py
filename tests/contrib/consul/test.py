import os

import consul
from wrapt import BoundFunctionWrapper

from ddtrace.contrib.internal.consul.patch import patch
from ddtrace.contrib.internal.consul.patch import unpatch
from ddtrace.ext import consul as consulx
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.trace import Pin
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import CONSUL_CONFIG


CONSUL_HTTP_ADDR = f"{CONSUL_CONFIG['host']}:{CONSUL_CONFIG['port']}"


class TestConsulPatch(TracerTestCase):
    TEST_SERVICE = "test-consul"

    def setUp(self):
        if "CONSUL_HTTP_ADDR" in os.environ:
            del os.environ["CONSUL_HTTP_ADDR"]
        super(TestConsulPatch, self).setUp()
        patch()
        c = consul.Consul(
            host=CONSUL_CONFIG["host"],
            port=CONSUL_CONFIG["port"],
        )
        Pin.override(consul.Consul, service=self.TEST_SERVICE, tracer=self.tracer)
        Pin.override(consul.Consul.KV, service=self.TEST_SERVICE, tracer=self.tracer)
        self.c = c

    def tearDown(self):
        unpatch()
        super(TestConsulPatch, self).tearDown()

    def test_put(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.span_type == "http"
        assert span.service == self.TEST_SERVICE
        assert span.name == consulx.CMD
        assert span.resource == "PUT"
        assert span.error == 0
        tags = {
            consulx.KEY: key,
            consulx.CMD: "PUT",
            "component": "consul",
            "span.kind": "client",
            "out.host": "127.0.0.1",
        }
        for k, v in tags.items():
            assert span.get_tag(k) == v

    def test_get(self):
        key = "test/get/consul"

        self.c.kv.get(key)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.span_type == "http"
        assert span.service == self.TEST_SERVICE
        assert span.name == consulx.CMD
        assert span.resource == "GET"
        assert span.error == 0
        tags = {
            consulx.KEY: key,
            consulx.CMD: "GET",
            "component": "consul",
            "span.kind": "client",
            "out.host": "127.0.0.1",
        }
        for k, v in tags.items():
            assert span.get_tag(k) == v

    def test_delete(self):
        key = "test/delete/consul"

        self.c.kv.delete(key)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.span_type == "http"
        assert span.service == self.TEST_SERVICE
        assert span.name == consulx.CMD
        assert span.resource == "DELETE"
        assert span.error == 0
        tags = {
            consulx.KEY: key,
            consulx.CMD: "DELETE",
            "component": "consul",
            "span.kind": "client",
            "out.host": "127.0.0.1",
        }
        for k, v in tags.items():
            assert span.get_tag(k) == v

    def test_kwargs(self):
        key = "test/kwargs/consul"
        value = "test_value"

        self.c.kv.put(key=key, value=value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert_is_measured(span)
        assert span.span_type == "http"
        assert span.service == self.TEST_SERVICE
        assert span.name == consulx.CMD
        assert span.resource == "PUT"
        assert span.error == 0
        tags = {
            consulx.KEY: key,
            "component": "consul",
            "span.kind": "client",
            "out.host": "127.0.0.1",
        }
        for k, v in tags.items():
            assert span.get_tag(k) == v

    def test_patch_idempotence(self):
        key = "test/patch/idempotence"

        patch()
        patch()

        self.c.kv.get(key)
        assert self.spans
        assert isinstance(self.c.kv.get, BoundFunctionWrapper)

        unpatch()
        self.reset()

        self.c.kv.get(key)
        assert not self.spans
        assert not isinstance(self.c.kv.get, BoundFunctionWrapper)

    def test_patch_preserves_functionality(self):
        key = "test/functionality"
        value = b"test_value"

        self.c.kv.put(key, value)
        _, data = self.c.kv.get(key)
        assert data["Value"] == value
        self.c.kv.delete(key)
        _, data = self.c.kv.get(key)
        assert data is None


class TestSchematization(TracerTestCase):
    def setUp(self):
        super(TestSchematization, self).setUp()
        patch()
        c = consul.Consul(
            host=CONSUL_CONFIG["host"],
            port=CONSUL_CONFIG["port"],
        )
        Pin.override(consul.Consul, tracer=self.tracer)
        Pin.override(consul.Consul.KV, tracer=self.tracer)
        self.c = c

    def tearDown(self):
        unpatch()
        super(TestSchematization, self).tearDown()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", CONSUL_HTTP_ADDR=CONSUL_HTTP_ADDR))
    def test_schematize_service_name_default(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "consul"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0", CONSUL_HTTP_ADDR=CONSUL_HTTP_ADDR)
    )
    def test_schematize_service_name_v0(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "consul"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1", CONSUL_HTTP_ADDR=CONSUL_HTTP_ADDR)
    )
    def test_schematize_service_name_v1(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(CONSUL_HTTP_ADDR=CONSUL_HTTP_ADDR))
    def test_schematize_unspecified_service_name_default(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "consul"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0", CONSUL_HTTP_ADDR=CONSUL_HTTP_ADDR)
    )
    def test_schematize_unspecified_service_name_v0(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.service == "consul"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1", CONSUL_HTTP_ADDR=CONSUL_HTTP_ADDR)
    )
    def test_schematize_unspecified_service_name_v1(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0", CONSUL_HTTP_ADDR=CONSUL_HTTP_ADDR)
    )
    def test_schematize_operation_name_v0(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.name == consulx.CMD

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1", CONSUL_HTTP_ADDR=CONSUL_HTTP_ADDR)
    )
    def test_schematize_operation_name_v1(self):
        key = "test/put/consul"
        value = "test_value"

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.name == "http.client.request"
