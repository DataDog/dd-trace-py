#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

from ddtrace.contrib.redis import missing_modules

if missing_modules:
    raise unittest.SkipTest("Missing dependencies %s" % missing_modules)

import redis
from nose.tools import eq_, ok_

from ddtrace.tracer import Tracer
from ddtrace.contrib.redis import get_traced_redis, get_traced_redis_from

from ...test_tracer import DummyWriter


class RedisTest(unittest.TestCase):
    SERVICE = 'test-cache'

    def setUp(self):
        """ purge redis """
        r = redis.Redis()
        r.flushall()

    def tearDown(self):
        r = redis.Redis()
        r.flushall()

    def test_basic_class(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        TracedRedisCache = get_traced_redis(tracer, service=self.SERVICE)
        r = TracedRedisCache()

        us = r.get('cheese')
        eq_(us, None)
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.name, 'redis.command')
        eq_(span.span_type, 'redis')
        eq_(span.error, 0)
        eq_(span.meta, {'out.host': u'localhost', 'redis.raw_command': u'GET cheese', 'out.port': u'6379', 'redis.args_length': u'2', 'out.redis_db': u'0'})
        eq_(span.resource, 'GET cheese')

        services = writer.pop_services()
        expected = {
            self.SERVICE: {"app":"redis", "app_type":"db"}
        }
        eq_(services, expected)


    def test_meta_override(self):
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        TracedRedisCache = get_traced_redis(tracer, service=self.SERVICE, meta={'cheese': 'camembert'})
        r = TracedRedisCache()

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
        r = TracedRedisCache()

        with r.pipeline() as p:
            p.set('blah', 32)
            p.rpush('foo', u'éé')
            p.hgetall('xxx')

            p.execute()

        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.name, 'redis.pipeline')
        eq_(span.span_type, 'redis')
        eq_(span.error, 0)
        eq_(span.get_tag('out.redis_db'), '0')
        eq_(span.get_tag('out.host'), 'localhost')
        ok_(float(span.get_tag('redis.pipeline_age')) > 0)
        eq_(span.get_tag('redis.pipeline_length'), '3')
        eq_(span.get_tag('out.port'), '6379')
        eq_(span.resource, u'SET blah 32\nRPUSH foo éé\nHGETALL xxx')
        eq_(span.get_tag('redis.raw_command'), u'SET blah 32\nRPUSH foo éé\nHGETALL xxx')

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
        r = TracedRedisCache()

        r.set('foo', 42)
        resp = r.get('foo')
        eq_(resp, 'YO42YO')

        spans = writer.pop()
        eq_(len(spans), 2)
        eq_(spans[0].name, 'redis.command')
        eq_(spans[0].resource, 'SET foo 42')
        eq_(spans[1].name, 'redis.command')
        eq_(spans[1].resource, 'GET foo')
