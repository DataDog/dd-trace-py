# -*- coding: utf-8 -*-
import unittest

from ddtrace.contrib.redis import missing_modules

if missing_modules:
    raise unittest.SkipTest("Missing dependencies %s" % missing_modules)

import redis
from nose.tools import eq_, ok_

from ddtrace.contrib.redis import get_traced_redis, get_traced_redis_from
from ddtrace import Pin, Tracer

from ..config import REDIS_CONFIG
from ...test_tracer import DummyWriter


class RedisTest(unittest.TestCase):
    SERVICE = 'test-cache'
    TEST_PORT = str(REDIS_CONFIG['port'])

    def setUp(self):
        """ purge redis """
        r = redis.Redis(port=REDIS_CONFIG['port'])
        r.flushall()

    def tearDown(self):
        r = redis.Redis(port=REDIS_CONFIG['port'])
        r.flushall()

    def test_long_command(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        TracedRedisCache = get_traced_redis(tracer, service=self.SERVICE)
        r = TracedRedisCache(port=REDIS_CONFIG['port'])

        long_cmd = "mget %s" % " ".join(map(str, range(1000)))
        us = r.execute_command(long_cmd)

        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
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
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer
        TracedRedisCache = get_traced_redis(tracer, service=self.SERVICE)
        r = TracedRedisCache(port=REDIS_CONFIG['port'])
        _assert_conn_traced(r, tracer, self.SERVICE)

    def test_meta_override(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        TracedRedisCache = get_traced_redis(tracer, service=self.SERVICE, meta={'cheese': 'camembert'})
        r = TracedRedisCache(port=REDIS_CONFIG['port'])

        r.get('cheese')
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        ok_('cheese' in span.meta and span.meta['cheese'] == 'camembert')

    def test_basic_class_pipeline(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        TracedRedisCache = get_traced_redis(tracer, service=self.SERVICE)
        r = TracedRedisCache(port=REDIS_CONFIG['port'])
        _assert_pipeline_traced(r, tracer, self.SERVICE)
        _assert_pipeline_immediate(r, tracer, self.SERVICE)

    def test_monkeypatch(self):
        from ddtrace.contrib.redis import patch

        suite = [
            _assert_conn_traced,
            _assert_pipeline_traced,
            _assert_pipeline_immediate,
        ]

        for func in suite:
            tracer = Tracer()
            tracer.writer = DummyWriter()
            r = patch.patch_client(redis.Redis(port=REDIS_CONFIG['port']))
            Pin(service=self.SERVICE, tracer=tracer).onto(r)
            func(r, service=self.SERVICE, tracer=tracer)

    def test_custom_class(self):
        class MyCustomRedis(redis.Redis):
            def execute_command(self, *args, **kwargs):
                response = super(MyCustomRedis, self).execute_command(*args, **kwargs)
                # py3 compat
                if isinstance(response, bytes):
                    response = response.decode('utf-8')
                return 'YO%sYO' % response


        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        TracedRedisCache = get_traced_redis_from(tracer, MyCustomRedis, service=self.SERVICE)
        r = TracedRedisCache(port=REDIS_CONFIG['port'])

        r.set('foo', 42)
        resp = r.get('foo')
        eq_(resp, 'YO42YO')

        spans = writer.pop()
        eq_(len(spans), 2)
        eq_(spans[0].name, 'redis.command')
        eq_(spans[0].resource, 'SET foo 42')
        eq_(spans[1].name, 'redis.command')
        eq_(spans[1].resource, 'GET foo')

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
    r = conn
    writer = tracer.writer
    with r.pipeline(transaction=False) as p:
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
    #ok_(span.get_metric('redis.pipeline_age') > 0)
    eq_(span.get_metric('redis.pipeline_length'), 3)

def _assert_conn_traced(conn, tracer, service):
    r = conn
    us = r.get('cheese')
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

    # services = writer.pop_services()
    # expected = {
    #     self.SERVICE: {"app": "redis", "app_type": "db"}
    # }
    # eq_(services, expected)

