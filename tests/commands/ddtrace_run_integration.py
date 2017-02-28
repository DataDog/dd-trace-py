"""
An integration test that uses a real Redis client
that we expect to be implicitly traced via `ddtrace-run`
"""

from __future__ import print_function

import redis
import os

from ddtrace import Pin
from tests.contrib.config import REDIS_CONFIG
from tests.test_tracer import DummyWriter

from nose.tools import eq_, ok_

if __name__ == '__main__':
    r = redis.Redis(port=REDIS_CONFIG['port'])
    pin = Pin.get_from(r)
    ok_(pin)
    eq_(pin.app, 'redis')
    eq_(pin.service, 'redis')

    pin.tracer.writer = DummyWriter()
    r.flushall()
    spans = pin.tracer.writer.pop()

    eq_(len(spans), 1)
    eq_(spans[0].service, 'redis')
    eq_(spans[0].resource, 'FLUSHALL')

    long_cmd = "mget %s" % " ".join(map(str, range(1000)))
    us = r.execute_command(long_cmd)

    spans = pin.tracer.writer.pop()
    eq_(len(spans), 1)
    span = spans[0]
    eq_(span.service, 'redis')
    eq_(span.name, 'redis.command')
    eq_(span.span_type, 'redis')
    eq_(span.error, 0)
    meta = {
        'out.host': u'localhost',
        'out.port': str(REDIS_CONFIG['port']),
        'out.redis_db': u'0',
    }
    for k, v in meta.items():
        eq_(span.get_tag(k), v)

    assert span.get_tag('redis.raw_command').startswith(u'mget 0 1 2 3')
    assert span.get_tag('redis.raw_command').endswith(u'...')

    print("Test success")
