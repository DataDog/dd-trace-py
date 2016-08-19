# -*- coding: utf-8 -*-
import unittest

from nose.tools import eq_, ok_, assert_raises

from ddtrace.tracer import Tracer
from ddtrace.contrib.flask_cache import get_traced_cache

from flask import Flask

from ...test_tracer import DummyWriter


class FlaskCacheTest(unittest.TestCase):
    SERVICE = "test-flask-cache"

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
        eq_(span.resource, "GET")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_operation",
            "flask_cache.backend": "simple",
        }

        eq_(span.meta, expected_meta)

    def test_simple_cache_get_without_arguments(self):
        # initialize the dummy writer
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # wrong usage of a get()
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
        eq_(span.resource, "GET")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 1)

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
        eq_(span.resource, "SET")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_operation",
            "flask_cache.backend": "simple",
        }

        eq_(span.meta, expected_meta)

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
        eq_(span.resource, "ADD")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_number",
            "flask_cache.backend": "simple",
        }

        eq_(span.meta, expected_meta)

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
        eq_(span.resource, "DELETE")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": u"á_complex_operation",
            "flask_cache.backend": "simple",
        }

        eq_(span.meta, expected_meta)

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
        eq_(span.resource, "DELETE_MANY")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": "['complex_operation', 'another_complex_op']",
            "flask_cache.backend": "simple",
        }

        eq_(span.meta, expected_meta)

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
        eq_(span.resource, "CLEAR")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.backend": "simple",
        }

        eq_(span.meta, expected_meta)

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
        eq_(span.resource, "GET_MANY")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": "['first_complex_op', 'second_complex_op']",
            "flask_cache.backend": "simple",
        }

        eq_(span.meta, expected_meta)

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
        eq_(span.resource, "SET_MANY")
        eq_(span.name, "flask_cache.cmd")
        eq_(span.span_type, "flask_cache")
        eq_(span.error, 0)

        expected_meta = {
            "flask_cache.key": "['first_complex_op', 'second_complex_op']",
            "flask_cache.backend": "simple",
        }

        eq_(span.meta["flask_cache.backend"], "simple")
        ok_("first_complex_op" in span.meta["flask_cache.key"])
        ok_("second_complex_op" in span.meta["flask_cache.key"])
