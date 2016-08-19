import unittest

# project
from ddtrace.ext import net
from ddtrace.tracer import Tracer, Span
from ddtrace.contrib.flask_cache import get_traced_cache
from ddtrace.contrib.flask_cache import metadata as flaskx
from ddtrace.contrib.flask_cache.utils import _extract_conn_metas, _set_span_metas

# 3rd party
from flask import Flask


class FlaskCacheUtilsTest(unittest.TestCase):
    SERVICE = "test-flask-cache"

    def test_extract_connection_meta_redis(self):
        """
        It should extract the proper metadata for the Redis client
        """
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "redis"})
        # extract client data
        meta = _extract_conn_metas(traced_cache.cache._client)
        expected_meta = {'out.host': 'localhost', 'out.port': 6379}
        self.assertDictEqual(meta, expected_meta)

    def test_extract_connection_meta_memcached(self):
        """
        It should extract the proper metadata for the Memcached client
        """
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "memcached"})
        # extract client data
        meta = _extract_conn_metas(traced_cache.cache._client)
        expected_meta = {'flask_cache.contact_points': ['127.0.0.1'], 'out.host': '127.0.0.1', 'out.port': 11211}
        self.assertDictEqual(meta, expected_meta)

    def test_extract_connection_meta_memcached_multiple(self):
        """
        It should extract the proper metadata for the Memcached client even with a pool of address
        """
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": [
                ("127.0.0.1", 11211),
                ("localhost", 11211),
            ],
        }
        traced_cache = Cache(app, config=config)
        # extract client data
        meta = _extract_conn_metas(traced_cache.cache._client)
        expected_meta = {
            'out.host': '127.0.0.1',
            'out.port': 11211,
            'flask_cache.contact_points': ['127.0.0.1', 'localhost'],
        }
        self.assertDictEqual(meta, expected_meta)

    def test_set_span_metas_simple(self):
        """
        It should set the default span attributes and meta
        """
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "simple"})
        # create a fake span
        span = Span(tracer, "test.command")
        # set the span attributes
        _set_span_metas(traced_cache, span, resource="GET")
        self.assertEqual(span.resource, "GET")
        self.assertEqual(span.service, traced_cache._datadog_service)
        self.assertEqual(span.span_type, flaskx.TYPE)
        self.assertEqual(span.meta[flaskx.CACHE_BACKEND], "simple")
        self.assertNotIn(flaskx.CONTACT_POINTS, span.meta)
        self.assertNotIn(net.TARGET_HOST, span.meta)
        self.assertNotIn(net.TARGET_PORT, span.meta)

    def test_set_span_metas_redis(self):
        """
        It should set the host and port Redis meta
        """
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "redis"})
        # create a fake span
        span = Span(tracer, "test.command")
        # set the span attributes
        _set_span_metas(traced_cache, span, resource="GET")
        self.assertEqual(span.resource, "GET")
        self.assertEqual(span.service, traced_cache._datadog_service)
        self.assertEqual(span.span_type, flaskx.TYPE)
        self.assertEqual(span.meta[flaskx.CACHE_BACKEND], "redis")
        self.assertEqual(span.meta[net.TARGET_HOST], 'localhost')
        self.assertEqual(span.meta[net.TARGET_PORT], '6379')
        self.assertNotIn(flaskx.CONTACT_POINTS, span.meta)

    def test_set_span_metas_memcached(self):
        """
        It should set the host and port Memcached meta
        """
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "memcached"})
        # create a fake span
        span = Span(tracer, "test.command")
        # set the span attributes
        _set_span_metas(traced_cache, span, resource="GET")
        self.assertEqual(span.resource, "GET")
        self.assertEqual(span.service, traced_cache._datadog_service)
        self.assertEqual(span.span_type, flaskx.TYPE)
        self.assertEqual(span.meta[flaskx.CACHE_BACKEND], "memcached")
        self.assertEqual(span.meta[net.TARGET_HOST], "127.0.0.1")
        self.assertEqual(span.meta[net.TARGET_PORT], "11211")
        self.assertEqual(span.meta[flaskx.CONTACT_POINTS], "['127.0.0.1']")
