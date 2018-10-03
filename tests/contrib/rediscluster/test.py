# -*- coding: utf-8 -*-
import rediscluster
from nose.tools import eq_

from ddtrace import Pin
from ddtrace.contrib.rediscluster.patch import patch, unpatch
from ..config import REDISCLUSTER_CONFIG
from ...test_tracer import get_dummy_tracer


class TestRedisPatch(object):

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
        r = self._get_test_client()
        r.flushall()
        patch()

    def tearDown(self):
        unpatch()
        r = self._get_test_client()
        r.flushall()

    def test_basics(self):
        r, tracer = self.get_redis_and_tracer()
        _assert_conn_traced(r, tracer, self.TEST_SERVICE)

    def test_pipeline(self):
        r, tracer = self.get_redis_and_tracer()
        _assert_pipeline_traced(r, tracer, self.TEST_SERVICE)

    def get_redis_and_tracer(self):
        tracer = get_dummy_tracer()
        r = self._get_test_client()
        Pin.override(r, service=self.TEST_SERVICE, tracer=tracer)
        return r, tracer

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


def _assert_conn_traced(conn, tracer, service):
    us = conn.get('cheese')
    eq_(us, None)
    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.service, service)
    eq_(span.name, 'redis.command')
    eq_(span.span_type, 'redis')
    eq_(span.error, 0)
    eq_(span.get_tag('redis.raw_command'), u'GET cheese')
    eq_(span.get_metric('redis.args_length'), 2)
    eq_(span.resource, 'GET cheese')


def _assert_pipeline_traced(conn, tracer, service):
    writer = tracer.writer

    with conn.pipeline(transaction=False) as p:
        p.set('blah', 32)
        p.rpush('foo', u'éé')
        p.hgetall('xxx')
        p.execute()

    spans = writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.service, service)
    eq_(span.name, 'redis.command')
    eq_(span.resource, u'SET blah 32\nRPUSH foo éé\nHGETALL xxx')
    eq_(span.span_type, 'redis')
    eq_(span.error, 0)
    eq_(span.get_tag('redis.raw_command'), u'SET blah 32\nRPUSH foo éé\nHGETALL xxx')
    eq_(span.get_metric('redis.pipeline_length'), 3)
