import consul
from ddtrace import Pin
from ddtrace.vendor.wrapt import BoundFunctionWrapper
from ddtrace.contrib.consul.patch import patch, unpatch

from ..config import CONSUL_CONFIG
from ...base import BaseTracerTestCase


class TestConsulPatch(BaseTracerTestCase):

    TEST_SERVICE = 'test-consul'

    def setUp(self):
        super(TestConsulPatch, self).setUp()
        patch()
        c = consul.Consul(
                host=CONSUL_CONFIG['host'],
                port=CONSUL_CONFIG['port'])
        Pin.override(consul.Consul, service=self.TEST_SERVICE, tracer=self.tracer)
        Pin.override(consul.Consul.KV, service=self.TEST_SERVICE, tracer=self.tracer)
        self.c = c

    def tearDown(self):
        unpatch()
        super(TestConsulPatch, self).tearDown()

    def test_put(self):
        key = 'test/put/consul'
        value = 'test_value'

        self.c.kv.put(key, value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.TEST_SERVICE
        assert span.name == 'Consul.KV.put'
        assert span.resource == key
        assert span.error == 0
        tags = {
            'consul.key': key,
        }
        for k, v in tags.items():
            assert span.get_tag(k) == v

    def test_get(self):
        key = 'test/get/consul'

        self.c.kv.get(key)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.TEST_SERVICE
        assert span.name == 'Consul.KV.get'
        assert span.resource == key
        assert span.error == 0
        tags = {
            'consul.key': key,
        }
        for k, v in tags.items():
            assert span.get_tag(k) == v

    def test_delete(self):
        key = 'test/delete/consul'

        self.c.kv.delete(key)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.TEST_SERVICE
        assert span.name == 'Consul.KV.delete'
        assert span.resource == key
        assert span.error == 0
        tags = {
            'consul.key': key,
        }
        for k, v in tags.items():
            assert span.get_tag(k) == v

    def test_kwargs(self):
        key = 'test/kwargs/consul'
        value = 'test_value'

        self.c.kv.put(key=key, value=value)

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.TEST_SERVICE
        assert span.name == 'Consul.KV.put'
        assert span.resource == key
        assert span.error == 0
        tags = {
            'consul.key': key,
        }
        for k, v in tags.items():
            assert span.get_tag(k) == v

    def test_patch_idempotence(self):
        key = 'test/patch/idempotence'

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
        key = 'test/functionality'
        value = b'test_value'

        self.c.kv.put(key, value)
        _, data = self.c.kv.get(key)
        assert data['Value'] == value
        self.c.kv.delete(key)
        _, data = self.c.kv.get(key)
        assert data is None
