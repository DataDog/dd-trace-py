# -*- coding: utf-8 -*-
from nose.tools import eq_, ok_
import redis

from ddtrace import Pin
from ddtrace.contrib.redis import get_traced_redis
from ddtrace.contrib.redis.patch import patch, unpatch

from ..config import REDIS_CONFIG
from ...test_tracer import get_dummy_tracer


class RedisCore(object):
    TEST_SERVICE = 'test-cache'
    TEST_PORT = str(REDIS_CONFIG['port'])

    def setUp(self):
        """ purge redis """
        r = redis.Redis(port=REDIS_CONFIG['port'])
        r.flushall()

    def tearDown(self):
        r = redis.Redis(port=REDIS_CONFIG['port'])
        r.flushall()

    def get_redis_and_tracer(self):
        # implement me
        pass

    def test_long_command(self):
        r, tracer = self.get_redis_and_tracer()

        long_cmd = "mget %s" % " ".join(map(str, range(1000)))
        us = r.execute_command(long_cmd)

        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'redis.command')
        eq_(span.span_type, 'redis')
        eq_(span.error, 0)
        meta = {
            'out.host': u'localhost',
            'out.port': self.TEST_PORT,
            'out.redis_db': u'0',
        }
        for k, v in meta.items():
            eq_(span.get_tag(k), v)

        assert span.get_tag('redis.raw_command').startswith(u'mget 0 1 2 3')
        assert span.get_tag('redis.raw_command').endswith(u'...')

    def test_basic_class(self):
        r, tracer = self.get_redis_and_tracer()
        _assert_conn_traced(r, tracer, self.TEST_SERVICE)

    def test_basic_class_pipeline(self):
        r, tracer = self.get_redis_and_tracer()
        _assert_pipeline_traced(r, tracer, self.TEST_SERVICE)
        _assert_pipeline_immediate(r, tracer, self.TEST_SERVICE)


class TestRedisLegacy(RedisCore):

    TEST_SERVICE = 'test-redis-legacy'

    def get_redis_and_tracer(self):
        tracer = get_dummy_tracer()

        TracedRedisCache = get_traced_redis(tracer, service=self.TEST_SERVICE)
        r = TracedRedisCache(port=REDIS_CONFIG['port'])

        return r, tracer


class TestRedisPatch(RedisCore):

    TEST_SERVICE = 'redis'

    def setUp(self):
        RedisCore.setUp(self)
        patch()

    def tearDown(self):
        unpatch()
        RedisCore.tearDown(self)

    def get_redis_and_tracer(self):
        tracer = get_dummy_tracer()

        r = redis.Redis(port=REDIS_CONFIG['port'])
        pin = Pin.get_from(r)
        assert pin, pin
        pin.tracer = tracer

        return r, tracer

    def test_meta_override(self):
        r, tracer = self.get_redis_and_tracer()

        Pin.get_from(r).tags = {'cheese': 'camembert'}
        r.get('cheese')
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        ok_('cheese' in span.meta and span.meta['cheese'] == 'camembert')

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        r = redis.Redis(port=REDIS_CONFIG['port'])
        Pin.get_from(r).tracer = tracer
        r.get("key")

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        r = redis.Redis(port=REDIS_CONFIG['port'])
        r.get("key")


        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = redis.Redis(port=REDIS_CONFIG['port'])
        Pin.get_from(r).tracer = tracer
        r.get("key")

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)


def _assert_pipeline_immediate(conn, tracer, service):
    r = conn
    writer = tracer.writer
    with r.pipeline() as p:
        p.set('a', 1)
        p.immediate_execute_command('SET', 'a', 1)
        p.execute()

    spans = writer.pop()
    eq_(len(spans), 2)
    span = spans[0]
    eq_(span.service, service)
    eq_(span.name, 'redis.command')
    eq_(span.resource, u'SET a 1')
    eq_(span.span_type, 'redis')
    eq_(span.error, 0)
    eq_(span.get_tag('out.redis_db'), '0')
    eq_(span.get_tag('out.host'), 'localhost')

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
    eq_(span.get_tag('out.redis_db'), '0')
    eq_(span.get_tag('out.host'), 'localhost')
    eq_(span.get_tag('redis.raw_command'), u'SET blah 32\nRPUSH foo éé\nHGETALL xxx')
    eq_(span.get_metric('redis.pipeline_length'), 3)

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
    eq_(span.get_tag('out.redis_db'), '0')
    eq_(span.get_tag('out.host'), 'localhost')
    eq_(span.get_tag('redis.raw_command'), u'GET cheese')
    eq_(span.get_metric('redis.args_length'), 2)
    eq_(span.resource, 'GET cheese')
