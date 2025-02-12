# -*- coding: utf-8 -*-
import pytest
import redis

from ddtrace.contrib.internal.redis.patch import patch
from ddtrace.contrib.internal.redis.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.trace import Pin
from tests.contrib.config import REDISCLUSTER_CONFIG
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


@pytest.mark.skipif(redis.VERSION < (4, 1), reason="redis.cluster is not implemented in redis<4.1")
class TestRedisClusterPatch(TracerTestCase):
    TEST_HOST = REDISCLUSTER_CONFIG["host"]
    TEST_PORTS = REDISCLUSTER_CONFIG["ports"]

    def _get_test_client(self):
        startup_nodes = [redis.cluster.ClusterNode(self.TEST_HOST, int(port)) for port in self.TEST_PORTS.split(",")]
        return redis.cluster.RedisCluster(startup_nodes=startup_nodes)

    def setUp(self):
        super(TestRedisClusterPatch, self).setUp()
        patch()
        r = self._get_test_client()
        r.flushall()
        Pin._override(r, tracer=self.tracer)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestRedisClusterPatch, self).tearDown()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_span_service_name_v1(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_tag("redis.raw_command") == "GET cheese"
        assert span.get_tag("component") == "redis"
        assert span.get_tag("db.system") == "redis"
        assert span.get_metric("redis.args_length") == 2
        assert span.resource == "GET"

    def test_unicode(self):
        us = self.r.get("😐")
        assert us is None
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_tag("redis.raw_command") == "GET 😐"
        assert span.get_tag("component") == "redis"
        assert span.get_tag("db.system") == "redis"
        assert span.get_metric("redis.args_length") == 2
        assert span.resource == "GET"

    def test_pipeline(self):
        with self.r.pipeline(transaction=False) as p:
            p.set("blah", 32)
            p.rpush("foo", "éé")
            p.hgetall("xxx")
            p.execute()

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.resource == "SET\nRPUSH\nHGETALL"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_tag("redis.raw_command") == "SET blah 32\nRPUSH foo éé\nHGETALL xxx"
        assert span.get_tag("component") == "redis"
        assert span.get_metric("redis.pipeline_length") == 3

    def test_patch_unpatch(self):
        tracer = DummyTracer()

        # Test patch idempotence
        patch()
        patch()

        r = self._get_test_client()
        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        r = self._get_test_client()
        r.get("key")

        spans = tracer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = self._get_test_client()
        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        """
        When a user specifies a service for the app
            The rediscluster integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        r = self._get_test_client()
        Pin.get_from(r)._clone(tracer=self.tracer).onto(r)
        r.get("key")

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        """
        When a user specifies a service for the app
            The rediscluster integration should use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        r = self._get_test_client()
        Pin.get_from(r)._clone(tracer=self.tracer).onto(r)
        r.get("key")

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_env_user_specified_rediscluster_service_v0(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myrediscluster", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_env_user_specified_rediscluster_service_v1(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myrediscluster", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="app-svc", DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_service_precedence_v0(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myrediscluster"

        self.reset()

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="app-svc", DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_service_precedence_v1(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myrediscluster"

        self.reset()
