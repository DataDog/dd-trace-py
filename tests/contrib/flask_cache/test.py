# -*- coding: utf-8 -*-
from flask import Flask

from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.flask_cache import get_traced_cache
from ddtrace.contrib.flask_cache.tracers import CACHE_BACKEND
from ddtrace.ext import net
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_dict_issuperset
from tests.utils import assert_is_measured

from ..config import MEMCACHED_CONFIG
from ..config import REDIS_CONFIG


class FlaskCacheTest(TracerTestCase):
    SERVICE = "test-flask-cache"
    TEST_REDIS_PORT = REDIS_CONFIG["port"]
    TEST_MEMCACHED_PORT = MEMCACHED_CONFIG["port"]

    def setUp(self):
        super(FlaskCacheTest, self).setUp()

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        self.cache = Cache(app, config={"CACHE_TYPE": "simple"})

    def test_simple_cache_get(self):
        self.cache.get("á_complex_operation")
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "get")
        self.assertEqual(span.name, "flask_cache.cmd")
        self.assertEqual(span.span_type, "cache")
        self.assertEqual(span.error, 0)

        expected_meta = {
            "flask_cache.key": "á_complex_operation",
            "flask_cache.backend": "simple",
            "component": "flask_cache",
        }

        assert_dict_issuperset(span.get_tags(), expected_meta)

    def test_simple_cache_get_rowcount_existing_key(self):
        self.cache.set("á_complex_operation", "with_á_value\nin two lines")
        self.cache.get("á_complex_operation")

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        get_span = spans[1]

        self.assertEqual(get_span.service, self.SERVICE)
        self.assertEqual(get_span.resource, "get")

        assert_dict_issuperset(get_span.get_metrics(), {"db.row_count": 1})

    def test_simple_cache_get_rowcount_missing_key(self):
        self.cache.get("á_complex_operation")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        get_span = spans[0]

        self.assertEqual(get_span.service, self.SERVICE)
        self.assertEqual(get_span.resource, "get")

        assert_dict_issuperset(get_span.get_metrics(), {"db.row_count": 0})

    def test_simple_cache_set(self):
        self.cache.set("á_complex_operation", "with_á_value\nin two lines")
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "set")
        self.assertEqual(span.name, "flask_cache.cmd")
        self.assertEqual(span.span_type, "cache")
        self.assertEqual(span.error, 0)

        expected_meta = {
            "flask_cache.key": "á_complex_operation",
            "flask_cache.backend": "simple",
            "component": "flask_cache",
        }

        assert_dict_issuperset(span.get_tags(), expected_meta)

    def test_simple_cache_add(self):
        self.cache.add("á_complex_number", 50)
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "add")
        self.assertEqual(span.name, "flask_cache.cmd")
        self.assertEqual(span.span_type, "cache")
        self.assertEqual(span.error, 0)

        expected_meta = {
            "flask_cache.key": "á_complex_number",
            "flask_cache.backend": "simple",
            "component": "flask_cache",
        }

        assert_dict_issuperset(span.get_tags(), expected_meta)

    def test_simple_cache_delete(self):
        self.cache.delete("á_complex_operation")
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "delete")
        self.assertEqual(span.name, "flask_cache.cmd")
        self.assertEqual(span.span_type, "cache")
        self.assertEqual(span.error, 0)

        expected_meta = {
            "flask_cache.key": "á_complex_operation",
            "flask_cache.backend": "simple",
            "component": "flask_cache",
        }

        assert_dict_issuperset(span.get_tags(), expected_meta)

    def test_simple_cache_delete_many(self):
        self.cache.delete_many("complex_operation", "another_complex_op")
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "delete_many")
        self.assertEqual(span.name, "flask_cache.cmd")
        self.assertEqual(span.span_type, "cache")
        self.assertEqual(span.error, 0)

        expected_meta = {
            "flask_cache.key": "['complex_operation', 'another_complex_op']",
            "flask_cache.backend": "simple",
            "component": "flask_cache",
        }

        assert_dict_issuperset(span.get_tags(), expected_meta)

    def test_simple_cache_clear(self):
        self.cache.clear()
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "clear")
        self.assertEqual(span.name, "flask_cache.cmd")
        self.assertEqual(span.span_type, "cache")
        self.assertEqual(span.error, 0)

        expected_meta = {
            "flask_cache.backend": "simple",
            "component": "flask_cache",
        }

        assert_dict_issuperset(span.get_tags(), expected_meta)

    def test_simple_cache_get_many(self):
        self.cache.get_many("first_complex_op", "second_complex_op")
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "get_many")
        self.assertEqual(span.name, "flask_cache.cmd")
        self.assertEqual(span.span_type, "cache")
        self.assertEqual(span.error, 0)

        expected_meta = {
            "flask_cache.key": "['first_complex_op', 'second_complex_op']",
            "flask_cache.backend": "simple",
            "component": "flask_cache",
        }

        assert_dict_issuperset(span.get_tags(), expected_meta)

    def test_simple_cache_get_many_rowcount_all_existing(self):
        self.cache.set_many(
            {
                "first_complex_op": 10,
                "second_complex_op": 20,
            }
        )
        self.cache.get_many("first_complex_op", "second_complex_op")

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        get_span = spans[1]

        self.assertEqual(get_span.service, self.SERVICE)
        self.assertEqual(get_span.resource, "get_many")

        assert_dict_issuperset(get_span.get_metrics(), {"db.row_count": 2})

    def test_simple_cache_get_many_rowcount_1_existing(self):
        self.cache.set_many(
            {
                "first_complex_op": 10,
                "second_complex_op": 20,
            }
        )
        result = self.cache.get_many("first_complex_op", "missing_complex_op")

        assert len(result) == 2
        assert result[0] == 10
        assert result[1] is None

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        get_span = spans[1]

        self.assertEqual(get_span.service, self.SERVICE)
        self.assertEqual(get_span.resource, "get_many")

        assert_dict_issuperset(get_span.get_metrics(), {"db.row_count": 1})

    def test_simple_cache_get_many_rowcount_0_existing(self):
        self.cache.set_many(
            {
                "first_complex_op": 10,
                "second_complex_op": 20,
            }
        )
        result = self.cache.get_many("missing_complex_op1", "missing_complex_op2")

        assert len(result) == 2
        assert result[0] is None
        assert result[1] is None

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        get_span = spans[1]

        self.assertEqual(get_span.service, self.SERVICE)
        self.assertEqual(get_span.resource, "get_many")

        assert_dict_issuperset(get_span.get_metrics(), {"db.row_count": 0})

    def test_simple_cache_set_many(self):
        self.cache.set_many(
            {
                "first_complex_op": 10,
                "second_complex_op": 20,
            }
        )
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "set_many")
        self.assertEqual(span.name, "flask_cache.cmd")
        self.assertEqual(span.span_type, "cache")
        self.assertEqual(span.error, 0)

        self.assertEqual(span.get_tag("flask_cache.backend"), "simple")
        self.assertTrue("first_complex_op" in span.get_tag("flask_cache.key"))
        self.assertTrue("second_complex_op" in span.get_tag("flask_cache.key"))

    def test_default_span_tags(self):
        # test tags and attributes
        with self.cache._TracedCache__trace("flask_cache.cmd") as span:
            self.assertEqual(span.service, self.SERVICE)
            self.assertEqual(span.span_type, "cache")
            self.assertEqual(span.get_tag(CACHE_BACKEND), "simple")
            self.assertTrue(net.TARGET_HOST not in span.get_tags())
            self.assertTrue("network.destination.port" not in span.get_tags())

    def test_default_span_tags_for_redis(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_PORT": self.TEST_REDIS_PORT,
        }
        cache = Cache(app, config=config)
        # test tags and attributes
        with cache._TracedCache__trace("flask_cache.cmd") as span:
            self.assertEqual(span.service, self.SERVICE)
            self.assertEqual(span.span_type, "cache")
            self.assertEqual(span.get_tag(CACHE_BACKEND), "redis")
            self.assertEqual(span.get_tag(net.TARGET_HOST), "localhost")
            self.assertEqual(span.get_metric("network.destination.port"), self.TEST_REDIS_PORT)

    def test_default_span_tags_memcached(self):
        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        config = {
            "CACHE_TYPE": "memcached",
            "CACHE_MEMCACHED_SERVERS": ["127.0.0.1:{}".format(self.TEST_MEMCACHED_PORT)],
        }
        cache = Cache(app, config=config)
        # test tags and attributes
        with cache._TracedCache__trace("flask_cache.cmd") as span:
            self.assertEqual(span.service, self.SERVICE)
            self.assertEqual(span.span_type, "cache")
            self.assertEqual(span.get_tag(CACHE_BACKEND), "memcached")
            self.assertEqual(span.get_tag(net.TARGET_HOST), "127.0.0.1")
            self.assertEqual(span.get_metric("network.destination.port"), self.TEST_MEMCACHED_PORT)

    def test_simple_cache_get_ot(self):
        """OpenTracing version of test_simple_cache_get."""
        ot_tracer = init_tracer("my_svc", self.tracer)

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer, service=self.SERVICE)
        app = Flask(__name__)
        cache = Cache(app, config={"CACHE_TYPE": "simple"})

        with ot_tracer.start_active_span("ot_span"):
            cache.get("á_complex_operation")

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        self.assertIsNone(ot_span.parent_id)
        self.assertEqual(dd_span.parent_id, ot_span.span_id)

        self.assertEqual(ot_span.resource, "ot_span")
        self.assertEqual(ot_span.service, "my_svc")

        assert_is_measured(dd_span)
        self.assertEqual(dd_span.service, self.SERVICE)
        self.assertEqual(dd_span.resource, "get")
        self.assertEqual(dd_span.name, "flask_cache.cmd")
        self.assertEqual(dd_span.span_type, "cache")
        self.assertEqual(dd_span.error, 0)

        expected_meta = {
            "flask_cache.key": "á_complex_operation",
            "flask_cache.backend": "simple",
            "component": "flask_cache",
        }

        assert_dict_issuperset(dd_span.get_tags(), expected_meta)

    def test_analytics_default(self):
        self.cache.get("á_complex_operation")
        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("flask_cache", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            self.cache.get("á_complex_operation")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("flask_cache", dict(analytics_enabled=True)):
            self.cache.get("á_complex_operation")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)


class TestFlaskCacheSchematization(TracerTestCase):
    TEST_REDIS_PORT = REDIS_CONFIG["port"]
    TEST_MEMCACHED_PORT = MEMCACHED_CONFIG["port"]

    def setUp(self):
        super(TestFlaskCacheSchematization, self).setUp()

        # create the TracedCache instance for a Flask app
        Cache = get_traced_cache(self.tracer)
        app = Flask(__name__)
        self.cache = Cache(app, config={"CACHE_TYPE": "simple"})

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematization_service_default(self):
        """
        When a service name is specified by the user
            The flask-cache integration should use it as the service name
        """

        self.cache.get("á_complex_operation")
        spans = self.get_spans()

        for span in spans:
            assert span.service == "mysvc", "Expected service name to be 'mysvc' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematization_service_v0(self):
        """
        When a service name is specified by the user
            The flask-cache integration should use it as the service name
        """

        self.cache.get("á_complex_operation")
        spans = self.get_spans()

        for span in spans:
            assert span.service == "mysvc", "Expected service name to be 'mysvc' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematization_service_v1(self):
        """
        When a service name is specified by the user
            The flask-cache integration should use it as the service name
        """

        self.cache.get("á_complex_operation")
        spans = self.get_spans()
        import os

        assert os.environ.get("DD_TRACE_SPAN_ATTRIBUTE_SCHEMA") == "v1"

        for span in spans:
            assert span.service == "mysvc", "Expected service name to be 'mysvc' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematization_undefined_service_default(self):
        """
        When a service name is specified by the user
            The flask-cache integration should use it as the service name
        """

        self.cache.get("á_complex_operation")
        spans = self.get_spans()

        for span in spans:
            assert span.service == "flask-cache", "Expected service name to be 'flask-cache' but was '{}'".format(
                span.service
            )

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematization_undefined_service_v0(self):
        """
        When a service name is specified by the user
            The flask-cache integration should use it as the service name
        """

        self.cache.get("á_complex_operation")
        spans = self.get_spans()

        for span in spans:
            assert span.service == "flask-cache", "Expected service name to be 'flask-cache' but was '{}'".format(
                span.service
            )

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematization_undefined_service_v1(self):
        """
        When a service name is specified by the user
            The flask-cache integration should use it as the service name
        """

        self.cache.get("á_complex_operation")
        spans = self.get_spans()

        for span in spans:
            assert (
                span.service == DEFAULT_SPAN_SERVICE_NAME
            ), "Expected service name to be 'internal.schema.DEFAULT_SEVICE_NAME' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematization_operation_name_v0(self):
        """
        When a service name is specified by the user
            The flask-cache integration should use it as the service name
        """

        self.cache.get("á_complex_operation")
        spans = self.get_spans()

        for span in spans:
            assert span.name == "flask_cache.cmd", "Expected span name to be 'flask_cache.command' but was '{}'".format(
                span.name
            )

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematization_operation_name_v1(self):
        """
        When a service name is specified by the user
            The flask-cache integration should use it as the service name
        """

        self.cache.get("á_complex_operation")
        spans = self.get_spans()

        for span in spans:
            assert (
                span.name == "flask_cache.command"
            ), "Expected span name to be 'flask_cache.command' but was '{}'".format(span.name)
