# flake8: noqa
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

# testing
from ..config import REDIS_CONFIG, MEMCACHED_CONFIG


class FlaskCacheUtilsTest(unittest.TestCase):
    SERVICE = "test-flask-cache"

    def test_extract_redis_connection_metadata(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": REDIS_CONFIG['port'],
        }
        traced_cache = Cache(app, config=config)
        # extract client data
        meta = _extract_conn_tags(traced_cache.cache._client)
        expected_meta = {'out.host': 'localhost', 'out.port': REDIS_CONFIG['port'], 'out.redis_db': 0}
        eq_(meta, expected_meta)

    def test_extract_memcached_connection_metadata(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": ["127.0.0.1:{}".format(MEMCACHED_CONFIG['port'])],
        }
        traced_cache = Cache(app, config=config)
        # extract client data
        meta = _extract_conn_tags(traced_cache.cache._client)
        expected_meta = {'out.host': '127.0.0.1', 'out.port': MEMCACHED_CONFIG['port']}
        eq_(meta, expected_meta)

    def test_extract_memcached_multiple_connection_metadata(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": [
                "127.0.0.1:{}".format(MEMCACHED_CONFIG['port']),
                "localhost:{}".format(MEMCACHED_CONFIG['port']),
            ],
        }
        traced_cache = Cache(app, config=config)
        # extract client data
        meta = _extract_conn_tags(traced_cache.cache._client)
        expected_meta = {
            'out.host': '127.0.0.1',
            'out.port': MEMCACHED_CONFIG['port'],
        }
        eq_(meta, expected_meta)

    def test_resource_from_cache_with_prefix(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": REDIS_CONFIG['port'],
            "CACHE_KEY_PREFIX": "users",
        }
        traced_cache = Cache(app, config=config)
        # expect a resource with a prefix
        expected_resource = "get users"
        resource = _resource_from_cache_prefix("GET", traced_cache.cache)
        eq_(resource, expected_resource)

    def test_resource_from_cache_with_empty_prefix(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": REDIS_CONFIG['port'],
            "CACHE_KEY_PREFIX": "",
        }
        traced_cache = Cache(app, config=config)
        # expect a resource with a prefix
        expected_resource = "get"
        resource = _resource_from_cache_prefix("GET", traced_cache.cache)
        eq_(resource, expected_resource)

    def test_resource_from_cache_without_prefix(self):
        # create the TracedCache instance for a Flask app
        tracer = Tracer()
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_REDIS_PORT": REDIS_CONFIG['port'],
            "CACHE_TYPE": "redis",
        }
        traced_cache = Cache(app, config={"CACHE_TYPE": "redis"})
        # expect only the resource name
        expected_resource = "get"
        resource = _resource_from_cache_prefix("GET", traced_cache.config)
        eq_(resource, expected_resource)
