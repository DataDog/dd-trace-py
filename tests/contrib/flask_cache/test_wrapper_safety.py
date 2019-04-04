# -*- coding: utf-8 -*-
import unittest

from nose.tools import eq_, ok_, assert_raises

# project
from ddtrace.ext import net
from ddtrace.tracer import Tracer
from ddtrace.contrib.flask_cache import get_traced_cache
from ddtrace.contrib.flask_cache.tracers import CACHE_BACKEND

# 3rd party
from flask import Flask
from redis.exceptions import ConnectionError

# testing
from ...test_tracer import DummyWriter


class FlaskCacheWrapperTest(unittest.TestCase):
    SERVICE = "test-flask-cache"

    def test_cache_get_without_arguments(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with assert_raises(TypeError) as ex:
            cache.get()

        # ensure that the error is not caused by our tracer
        ok_("get()" in ex.exception.args[0])
        ok_("argument" in ex.exception.args[0])
        spans = writer.pop()
        # an error trace must be sent
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "get")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 1)

    def test_cache_set_without_arguments(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with assert_raises(TypeError) as ex:
            cache.set()

        # ensure that the error is not caused by our tracer
        ok_("set()" in ex.exception.args[0])
        ok_("argument" in ex.exception.args[0])
        spans = writer.pop()
        # an error trace must be sent
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "set")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 1)

    def test_cache_add_without_arguments(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with assert_raises(TypeError) as ex:
            cache.add()

        # ensure that the error is not caused by our tracer
        ok_("add()" in ex.exception.args[0])
        ok_("argument" in ex.exception.args[0])
        spans = writer.pop()
        # an error trace must be sent
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "add")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 1)

    def test_cache_delete_without_arguments(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with assert_raises(TypeError) as ex:
            cache.delete()

        # ensure that the error is not caused by our tracer
        ok_("delete()" in ex.exception.args[0])
        ok_("argument" in ex.exception.args[0])
        spans = writer.pop()
        # an error trace must be sent
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "delete")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 1)

    def test_cache_set_many_without_arguments(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with assert_raises(TypeError) as ex:
            cache.set_many()

        # ensure that the error is not caused by our tracer
        ok_("set_many()" in ex.exception.args[0])
        ok_("argument" in ex.exception.args[0])
        spans = writer.pop()
        # an error trace must be sent
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "set_many")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 1)

    def test_redis_cache_tracing_with_a_wrong_connection(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": 2230,
            "CACHE_REDIS_HOST": "127.0.0.1"
        }
        cache = Cache(app, config=config)

        # use a wrong redis connection
        with assert_raises(ConnectionError) as ex:
            cache.get(u"á_complex_operation")

        print(ex.exception)
        # ensure that the error is not caused by our tracer
        ok_("127.0.0.1:2230. Connection refused." in ex.exception.args[0])
        spans = writer.pop()
        # an error trace must be sent
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "get")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.meta[CACHE_BACKEND], "redis")
        eq_(span.meta[net.TARGET_HOST], '127.0.0.1')
        eq_(span.meta[net.TARGET_PORT], '2230')
        eq_(span.error, 1)

    def test_memcached_cache_tracing_with_a_wrong_connection(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": ['localhost:2230'],
        }
        cache = Cache(app, config=config)

        # use a wrong memcached connection
        try:
            cache.get(u"á_complex_operation")
        except Exception:
            pass

        # ensure that the error is not caused by our tracer
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "get")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.meta[CACHE_BACKEND], "memcached")
        eq_(span.meta[net.TARGET_HOST], 'localhost')
        eq_(span.meta[net.TARGET_PORT], '2230')

        # the pylibmc backend raises an exception and memcached backend does
        # not, so don't test anything about the status.
