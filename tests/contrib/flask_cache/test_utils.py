import unittest

from flask import Flask

from ddtrace.contrib.internal.flask_cache.patch import get_traced_cache
from ddtrace.contrib.internal.flask_cache.utils import _extract_client
from ddtrace.contrib.internal.flask_cache.utils import _extract_conn_tags
from ddtrace.contrib.internal.flask_cache.utils import _resource_from_cache_prefix
from ddtrace.trace import tracer

from ..config import MEMCACHED_CONFIG
from ..config import REDIS_CONFIG


class FlaskCacheUtilsTest(unittest.TestCase):
    SERVICE = "test-flask-cache"

    def test_extract_redis_connection_metadata(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": REDIS_CONFIG["port"],
        }
        traced_cache = Cache(app, config=config)
        # extract client data
        meta = _extract_conn_tags(_extract_client(traced_cache.cache))
        expected_meta = {
            "out.host": "localhost",
            "network.destination.port": REDIS_CONFIG["port"],
            "out.redis_db": 0,
            "server.address": "localhost",
        }
        assert meta == expected_meta

    def test_extract_memcached_connection_metadata(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": ["127.0.0.1:{}".format(MEMCACHED_CONFIG["port"])],
        }
        traced_cache = Cache(app, config=config)
        # extract client data
        meta = _extract_conn_tags(_extract_client(traced_cache.cache))
        expected_meta = {
            "out.host": "127.0.0.1",
            "network.destination.port": MEMCACHED_CONFIG["port"],
            "server.address": "127.0.0.1",
        }
        assert meta == expected_meta

    def test_extract_memcached_multiple_connection_metadata(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": [
                "127.0.0.1:{}".format(MEMCACHED_CONFIG["port"]),
                "localhost:{}".format(MEMCACHED_CONFIG["port"]),
            ],
        }
        traced_cache = Cache(app, config=config)
        # extract client data
        meta = _extract_conn_tags(_extract_client(traced_cache.cache))
        expected_meta = {
            "out.host": "127.0.0.1",
            "network.destination.port": MEMCACHED_CONFIG["port"],
            "server.address": "127.0.0.1",
        }
        assert meta == expected_meta

    def test_resource_from_cache_with_prefix(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": REDIS_CONFIG["port"],
            "CACHE_KEY_PREFIX": "users",
        }
        traced_cache = Cache(app, config=config)
        # expect a resource with a prefix
        expected_resource = "get users"
        resource = _resource_from_cache_prefix("GET", traced_cache.cache)
        assert resource == expected_resource

    def test_resource_from_cache_with_empty_prefix(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": REDIS_CONFIG["port"],
            "CACHE_KEY_PREFIX": "",
        }
        traced_cache = Cache(app, config=config)
        # expect a resource with a prefix
        expected_resource = "get"
        resource = _resource_from_cache_prefix("GET", traced_cache.cache)
        assert resource == expected_resource

    def test_resource_from_cache_without_prefix(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(tracer, service=self.SERVICE)
        app = Flask(__name__)
        traced_cache = Cache(app, config={"CACHE_TYPE": "redis"})
        # expect only the resource name
        expected_resource = "get"
        resource = _resource_from_cache_prefix("GET", traced_cache.config)
        assert resource == expected_resource
