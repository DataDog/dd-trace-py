# -*- coding: utf-8 -*-
import types

import pytest
import redis

from ddtrace.contrib.internal.redis.patch import patch
from ddtrace.contrib.internal.redis.patch import unpatch
from ddtrace.contrib.internal.redis_utils import _build_tags
from ddtrace.ext import net
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.contrib.config import REDISCLUSTER_CONFIG
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from .utils import find_redis_span


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
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestRedisClusterPatch, self).tearDown()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_span_service_name_v1(self):
        us = self.r.get("cheese")
        assert us is None
        span = find_redis_span(self.get_spans(), resource="GET")
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None
        span = find_redis_span(self.get_spans(), resource="GET", raw_command="GET cheese")
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

    def test_connection_tags(self):
        """RedisCluster spans must include out.host and server.address for inferred entity resolution."""
        self.r.get("cheese")
        span = find_redis_span(self.get_spans(), resource="GET", raw_command="GET cheese")
        assert span.get_tag("out.host") is not None, "out.host tag should be set on RedisCluster spans"
        assert span.get_tag("server.address") is not None, "server.address tag should be set on RedisCluster spans"
        assert span.get_metric(net.TARGET_PORT) is not None

    def test_unicode(self):
        us = self.r.get("😐")
        assert us is None
        span = find_redis_span(self.get_spans(), resource="GET", raw_command="GET 😐")
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

    @pytest.mark.skipif(PYTHON_VERSION_INFO >= (3, 14), reason="fails under Python 3.14")
    def test_pipeline(self):
        with self.r.pipeline(transaction=False) as p:
            p.set("blah", 32)
            p.rpush("foo", "éé")
            p.hgetall("xxx")
            p.execute()

        span = find_redis_span(self.get_spans(), resource="SET\nRPUSH\nHGETALL")
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
        # Clear any spans from setUp (cluster discovery + flushall)
        self.pop_spans()

        # Test patch idempotence
        patch()
        patch()

        r = self._get_test_client()
        r.get("key")

        # Filter to only GET spans (ignore cluster discovery spans)
        spans = [s for s in self.pop_spans() if s.resource == "GET"]
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        r = self._get_test_client()
        r.get("key")

        spans = self.pop_spans()
        assert not spans, spans

        # Test patch again
        patch()

        r = self._get_test_client()
        r.get("key")

        # Filter to only GET spans (ignore cluster discovery spans)
        spans = [s for s in self.pop_spans() if s.resource == "GET"]
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
        r.get("key")

        span = find_redis_span(self.get_spans(), resource="GET")
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
        r.get("key")

        span = find_redis_span(self.get_spans(), resource="GET")
        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_env_user_specified_rediscluster_service_v0(self):
        self.r.get("cheese")
        span = find_redis_span(self.get_spans(), resource="GET", raw_command="GET cheese")
        assert span.service == "myrediscluster", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_env_user_specified_rediscluster_service_v1(self):
        self.r.get("cheese")
        span = find_redis_span(self.get_spans(), resource="GET")
        assert span.service == "myrediscluster", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="app-svc", DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_service_precedence_v0(self):
        self.r.get("cheese")
        span = find_redis_span(self.get_spans(), resource="GET", raw_command="GET cheese")
        assert span.service == "myrediscluster"

        self.reset()

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="app-svc", DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_service_precedence_v1(self):
        self.r.get("cheese")
        span = find_redis_span(self.get_spans(), resource="GET")
        assert span.service == "myrediscluster"

        self.reset()


_NODE = types.SimpleNamespace(host="redis-host", port=7000)


class TestClusterConnTagsUnit:
    """Unit tests for _build_tags cluster conn tag extraction — no live Redis required."""

    def _make_instance(self, startup_nodes):
        return types.SimpleNamespace(nodes_manager=types.SimpleNamespace(startup_nodes=startup_nodes))

    @pytest.mark.parametrize(
        "startup_nodes",
        [
            {"redis-host:7000": _NODE},  # redis-py 5.x dict
            [_NODE],  # redis-py 4.x list
        ],
    )
    def test_startup_nodes(self, startup_nodes):
        tags = _build_tags(None, self._make_instance(startup_nodes), "redis")
        assert tags[net.TARGET_HOST] == "redis-host"
        assert tags[net.SERVER_ADDRESS] == "redis-host"
        assert tags[net.TARGET_PORT] == 7000

    @pytest.mark.parametrize("startup_nodes", [{}, []])
    def test_empty_startup_nodes(self, startup_nodes):
        tags = _build_tags(None, self._make_instance(startup_nodes), "redis")
        assert net.TARGET_HOST not in tags
        assert net.SERVER_ADDRESS not in tags

    def test_startup_nodes_dict_values(self):
        """Cover redis-py versions that store startup_nodes values as plain dicts."""
        instance = self._make_instance({"redis-host:7000": {"host": "redis-host", "port": 7000}})
        tags = _build_tags(None, instance, "redis")
        assert tags[net.TARGET_HOST] == "redis-host"
        assert tags[net.SERVER_ADDRESS] == "redis-host"
        assert tags[net.TARGET_PORT] == 7000

    def test_missing_nodes_manager(self):
        tags = _build_tags(None, types.SimpleNamespace(), "redis")
        assert net.TARGET_HOST not in tags
        assert net.SERVER_ADDRESS not in tags
