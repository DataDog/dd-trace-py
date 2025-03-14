# -*- coding: utf-8 -*-
from flask import Flask
import pytest
from redis.exceptions import ConnectionError

from ddtrace.contrib.internal.flask_cache.patch import CACHE_BACKEND
from ddtrace.contrib.internal.flask_cache.patch import get_traced_cache
from ddtrace.ext import net
from tests.utils import TracerTestCase


class FlaskCacheWrapperTest(TracerTestCase):
    SERVICE = "test-flask-cache"

    def test_cache_get_without_arguments(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with pytest.raises(TypeError) as ex:
            cache.get()

        # ensure that the error is not caused by our tracer
        assert "get()" in ex.value.args[0]
        assert "argument" in ex.value.args[0]
        spans = self.get_spans()
        # an error trace must be sent
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.SERVICE
        assert span.resource == "get"
        assert span.name == "flask_cache.cmd"
        assert span.span_type == "cache"
        assert span.error == 1

    def test_cache_set_without_arguments(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with pytest.raises(TypeError) as ex:
            cache.set()

        # ensure that the error is not caused by our tracer
        assert "set()" in ex.value.args[0]
        assert "argument" in ex.value.args[0]
        spans = self.pop_spans()
        # an error trace must be sent
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.SERVICE
        assert span.resource == "set"
        assert span.name == "flask_cache.cmd"
        assert span.span_type == "cache"
        assert span.error == 1

    def test_cache_add_without_arguments(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with pytest.raises(TypeError) as ex:
            cache.add()

        # ensure that the error is not caused by our tracer
        assert "add()" in ex.value.args[0]
        assert "argument" in ex.value.args[0]
        spans = self.pop_spans()
        # an error trace must be sent
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.SERVICE
        assert span.resource == "add"
        assert span.name == "flask_cache.cmd"
        assert span.span_type == "cache"
        assert span.error == 1

    def test_cache_delete_without_arguments(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with pytest.raises(TypeError) as ex:
            cache.delete()

        # ensure that the error is not caused by our tracer
        assert "delete()" in ex.value.args[0]
        assert "argument" in ex.value.args[0]
        spans = self.pop_spans()
        # an error trace must be sent
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.SERVICE
        assert span.resource == "delete"
        assert span.name == "flask_cache.cmd"
        assert span.span_type == "cache"
        assert span.error == 1

    def test_cache_set_many_without_arguments(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        # make a wrong call
        with pytest.raises(TypeError) as ex:
            cache.set_many()

        # ensure that the error is not caused by our tracer
        assert "set_many()" in ex.value.args[0]
        assert "argument" in ex.value.args[0]
        spans = self.pop_spans()
        # an error trace must be sent
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.SERVICE
        assert span.resource == "set_many"
        assert span.name == "flask_cache.cmd"
        assert span.span_type == "cache"
        assert span.error == 1

    def test_redis_cache_tracing_with_a_wrong_connection(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {"CACHE_TYPE": "redis", "CACHE_REDIS_PORT": 2230, "CACHE_REDIS_HOST": "127.0.0.1"}
        cache = Cache(app, config=config)

        # use a wrong redis connection
        with pytest.raises(ConnectionError) as ex:
            cache.get("á_complex_operation")

        # ensure that the error is not caused by our tracer
        assert "127.0.0.1:2230. Connection refused." in ex.value.args[0]
        spans = self.pop_spans()
        # an error trace must be sent
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.SERVICE
        assert span.resource == "get"
        assert span.name == "flask_cache.cmd"
        assert span.span_type == "cache"
        assert span.get_tag(CACHE_BACKEND) == "redis"
        assert span.get_tag(net.TARGET_HOST) == "127.0.0.1"
        assert span.get_tag("component") == "flask_cache"
        assert span.get_metric("network.destination.port") == 2230
        assert span.error == 1

    def test_memcached_cache_tracing_with_a_wrong_connection(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": ["localhost:2230"],
        }
        cache = Cache(app, config=config)

        # use a wrong memcached connection
        try:
            cache.get("á_complex_operation")
        except Exception:
            pass

        # ensure that the error is not caused by our tracer
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == self.SERVICE
        assert span.resource == "get"
        assert span.name == "flask_cache.cmd"
        assert span.span_type == "cache"
        assert span.get_tag(CACHE_BACKEND) == "memcached"
        assert span.get_tag(net.TARGET_HOST) == "localhost"
        assert span.get_tag("component") == "flask_cache"
        assert span.get_metric("network.destination.port") == 2230

        # the pylibmc backend raises an exception and memcached backend does
        # not, so don't test anything about the status.
