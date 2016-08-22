import unittest

from nose.tools import eq_, ok_

# project
from ddtrace.ext import net
from ddtrace.tracer import Tracer, Span
from ddtrace.contrib.flask_cache import get_traced_cache
from ddtrace.contrib.flask_cache.utils import _extract_conn_tags, _resource_from_cache_prefix
from ddtrace.contrib.flask_cache.tracers import TYPE, CACHE_BACKEND

# 3rd party
from flask import Flask


class FlaskCacheUtilsTest(unittest.TestCase):
    SERVICE = "test-flask-cache"

    def test_extract_redis_connection_metadata(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "redis"})
        # extract client data
        meta = _extract_conn_tags(traced_cache.cache._client)
        expected_meta = {'out.host': 'localhost', 'out.port': 6379, 'out.redis_db': 0}
        eq_(meta, expected_meta)

    def test_extract_memcached_connection_metadata(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "memcached"})
        # extract client data
        meta = _extract_conn_tags(traced_cache.cache._client)
        expected_meta = {'out.host': '127.0.0.1', 'out.port': 11211}
        eq_(meta, expected_meta)

    def test_extract_memcached_multiple_connection_metadata(self):
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
        meta = _extract_conn_tags(traced_cache.cache._client)
        expected_meta = {
            'out.host': '127.0.0.1',
            'out.port': 11211,
        }
        eq_(meta, expected_meta)

    def test_resource_from_cache_with_prefix(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "redis", "CACHE_KEY_PREFIX": "users"})
        # expect a resource with a prefix
        expected_resource = "get users"
        resource = _resource_from_cache_prefix("GET", traced_cache.cache)
        eq_(resource, expected_resource)

    def test_resource_from_cache_with_empty_prefix(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "redis", "CACHE_KEY_PREFIX": ""})
        # expect a resource with a prefix
        expected_resource = "get"
        resource = _resource_from_cache_prefix("GET", traced_cache.cache)
        eq_(resource, expected_resource)

    def test_resource_from_cache_without_prefix(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "redis"})
        # expect only the resource name
        expected_resource = "get"
        resource = _resource_from_cache_prefix("GET", cache.config)
        eq_(resource, expected_resource)
