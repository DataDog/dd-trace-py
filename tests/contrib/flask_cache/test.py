# -*- coding: utf-8 -*-
import unittest

from nose.tools import eq_, ok_

# project
from ddtrace.ext import net
from ddtrace.tracer import Tracer
from ddtrace.contrib.flask_cache import get_traced_cache
from ddtrace.contrib.flask_cache.tracers import TYPE, CACHE_BACKEND

# 3rd party
from flask import Flask

# testing
from tests.opentracer.utils import init_tracer
from ..config import REDIS_CONFIG, MEMCACHED_CONFIG
from ...test_tracer import DummyWriter
from ...util import assert_dict_issuperset


class FlaskCacheTest(unittest.TestCase):
    SERVICE = "test-flask-cache"
    TEST_REDIS_PORT = str(REDIS_CONFIG['port'])
    TEST_MEMCACHED_PORT = str(MEMCACHED_CONFIG['port'])

    def test_simple_cache_get(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        cache.get(u"á_complex_operation")
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "get")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_operation",
            "flask_cache.backend": "simple",
        }

        assert_dict_issuperset(span.meta, expected_meta)

    def test_simple_cache_set(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        cache.set(u"á_complex_operation", u"with_á_value\nin two lines")
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "set")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_operation",
            "flask_cache.backend": "simple",
        }

        assert_dict_issuperset(span.meta, expected_meta)

    def test_simple_cache_add(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        cache.add(u"á_complex_number", 50)
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "add")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_number",
            "flask_cache.backend": "simple",
        }

        assert_dict_issuperset(span.meta, expected_meta)

    def test_simple_cache_delete(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        cache.delete(u"á_complex_operation")
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "delete")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_operation",
            "flask_cache.backend": "simple",
        }

        assert_dict_issuperset(span.meta, expected_meta)

    def test_simple_cache_delete_many(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        cache.delete_many("complex_operation", "another_complex_op")
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "delete_many")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": "['complex_operation', 'another_complex_op']",
            "flask_cache.backend": "simple",
        }

        assert_dict_issuperset(span.meta, expected_meta)

    def test_simple_cache_clear(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        cache.clear()
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "clear")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.backend": "simple",
        }

        assert_dict_issuperset(span.meta, expected_meta)

    def test_simple_cache_get_many(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        cache.get_many('first_complex_op', 'second_complex_op')
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "get_many")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": "['first_complex_op', 'second_complex_op']",
            "flask_cache.backend": "simple",
        }

        assert_dict_issuperset(span.meta, expected_meta)

    def test_simple_cache_set_many(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        cache.set_many({
            'first_complex_op': 10,
            'second_complex_op': 20,
        })
        spans = writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.SERVICE)
        eq_(span.resource, "set_many")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "cache")
        eq_(span.error, 0)

        eq_(span.meta["flask_cache.backend"], "simple")
        ok_("first_complex_op" in span.meta["flask_cache.key"])
        ok_("second_complex_op" in span.meta["flask_cache.key"])

    def test_default_span_tags(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})
        # test tags and attributes
        with cache._TracedCache__trace("flask_cache.cmd") as span:
            eq_(span.service, cache._datadog_service)
            eq_(span.span_type, TYPE)
            eq_(span.meta[CACHE_BACKEND], "simple")
            ok_(net.TARGET_HOST not in span.meta)
            ok_(net.TARGET_PORT not in span.meta)

    def test_default_span_tags_for_redis(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": self.TEST_REDIS_PORT,
        }
        cache = Cache(app, config=config)
        # test tags and attributes
        with cache._TracedCache__trace("flask_cache.cmd") as span:
            eq_(span.service, cache._datadog_service)
            eq_(span.span_type, TYPE)
            eq_(span.meta[CACHE_BACKEND], "redis")
            eq_(span.meta[net.TARGET_HOST], 'localhost')
            eq_(span.meta[net.TARGET_PORT], self.TEST_REDIS_PORT)

    def test_default_span_tags_memcached(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": ["127.0.0.1:{}".format(self.TEST_MEMCACHED_PORT)],
        }
        cache = Cache(app, config=config)
        # test tags and attributes
        with cache._TracedCache__trace("flask_cache.cmd") as span:
            eq_(span.service, cache._datadog_service)
            eq_(span.span_type, TYPE)
            eq_(span.meta[CACHE_BACKEND], "memcached")
            eq_(span.meta[net.TARGET_HOST], "127.0.0.1")
            eq_(span.meta[net.TARGET_PORT], self.TEST_MEMCACHED_PORT)

    def test_simple_cache_get_ot(self):
        """OpenTracing version of test_simple_cache_get."""
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        ot_tracer = init_tracer("my_svc", tracer)

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        with ot_tracer.start_active_span("ot_span"):
            cache.get(u"á_complex_operation")

        spans = writer.pop()
        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.resource, "ot_span")
        eq_(ot_span.service, "my_svc")

        eq_(dd_span.service, self.SERVICE)
        eq_(dd_span.resource, "get")
        eq_(dd_span.name, "flask_cache.cmd")
        eq_(dd_span.span_type, "cache")
        eq_(dd_span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_operation",
            "flask_cache.backend": "simple",
        }

        assert_dict_issuperset(dd_span.meta, expected_meta)
