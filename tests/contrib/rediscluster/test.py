# -*- coding: utf-8 -*-
import rediscluster
from nose.tools import eq_

from ddtrace import Pin
from ddtrace.contrib.rediscluster.patch import patch, unpatch
from ..config import REDISCLUSTER_CONFIG
from ...test_tracer import get_dummy_tracer
from ...base import BaseTracerTestCase


class TestRedisPatch(BaseTracerTestCase):

    TEST_SERVICE = 'rediscluster-patch'
    TEST_HOST = REDISCLUSTER_CONFIG['host']
    TEST_PORTS = REDISCLUSTER_CONFIG['ports']

    def _get_test_client(self):
        startup_nodes = [
            {'host': self.TEST_HOST, 'port': int(port)}
            for port in self.TEST_PORTS.split(',')
        ]
        return rediscluster.StrictRedisCluster(startup_nodes=startup_nodes)

    def setUp(self):
        super(TestRedisPatch, self).setUp()
        patch()
        r = self._get_test_client()
        r.flushall()
        Pin.override(r, service=self.TEST_SERVICE, tracer=self.tracer)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestRedisPatch, self).tearDown()

    def test_basics(self):
        us = self.r.get('cheese')
        eq_(us, None)
        spans = self.get_spans()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'redis.command')
        eq_(span.span_type, 'redis')
        eq_(span.error, 0)
        eq_(span.get_tag('redis.raw_command'), u'GET cheese')
        eq_(span.get_metric('redis.args_length'), 2)
        eq_(span.resource, 'GET cheese')

    def test_pipeline(self):
        with self.r.pipeline(transaction=False) as p:
            p.set('blah', 32)
            p.rpush('foo', u'éé')
            p.hgetall('xxx')
            p.execute()

        spans = self.get_spans()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'redis.command')
        eq_(span.resource, u'SET blah 32\nRPUSH foo éé\nHGETALL xxx')
        eq_(span.span_type, 'redis')
        eq_(span.error, 0)
        eq_(span.get_tag('redis.raw_command'), u'SET blah 32\nRPUSH foo éé\nHGETALL xxx')
        eq_(span.get_metric('redis.pipeline_length'), 3)

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        r = self._get_test_client()
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get('key')

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        r = self._get_test_client()
        r.get('key')

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = self._get_test_client()
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get('key')

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
