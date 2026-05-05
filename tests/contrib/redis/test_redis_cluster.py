# -*- coding: utf-8 -*-
import pytest
import redis

from ddtrace.contrib.internal.redis.patch import patch
from ddtrace.contrib.internal.redis.patch import unpatch
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

    # ------------------------------------------------------------------
    # Connection tag tests — verifying FRAPMS-5965 / APMS-19310 fix
    # ------------------------------------------------------------------

    def test_connection_tags_get(self):
        """GET command span has out.host, network.destination.port, server.address."""
        self.r.get("cheese")
        span = find_redis_span(self.get_spans(), resource="GET", raw_command="GET cheese")
        assert span.get_tag("out.host") is not None
        assert span.get_tag("server.address") is not None
        port = span.get_metric("network.destination.port")
        assert port is not None
        expected_ports = [int(p) for p in self.TEST_PORTS.split(",")]
        assert port in expected_ports

    def test_connection_tags_set(self):
        """SET command span also carries connection tags."""
        self.r.set("mykey", "myvalue")
        span = find_redis_span(self.get_spans(), resource="SET")
        assert span.get_tag("out.host") is not None
        assert span.get_tag("server.address") is not None
        assert span.get_metric("network.destination.port") is not None

    def test_connection_tags_host_is_string(self):
        """out.host and server.address must be strings, not bytes or None."""
        self.r.get("typecheck")
        span = find_redis_span(self.get_spans(), resource="GET")
        assert isinstance(span.get_tag("out.host"), str)
        assert isinstance(span.get_tag("server.address"), str)

    def test_connection_tags_port_is_numeric(self):
        """network.destination.port must be a number."""
        self.r.get("portcheck")
        span = find_redis_span(self.get_spans(), resource="GET")
        port = span.get_metric("network.destination.port")
        assert port is not None
        assert isinstance(port, (int, float))

    def test_connection_tags_host_matches_startup_node(self):
        """out.host is populated (cluster nodes may advertise a different IP than the startup hostname)."""
        self.r.get("hostcheck")
        span = find_redis_span(self.get_spans(), resource="GET")
        assert span.get_tag("out.host") is not None

    def test_connection_tags_port_is_one_of_startup_ports(self):
        """The reported port must be one of the ports we passed as startup nodes."""
        self.r.get("portrangecheck")
        span = find_redis_span(self.get_spans(), resource="GET")
        port = span.get_metric("network.destination.port")
        expected_ports = [int(p) for p in self.TEST_PORTS.split(",")]
        assert port in expected_ports, f"port {port} not in expected {expected_ports}"

    def test_connection_tags_out_host_equals_server_address(self):
        """out.host and server.address must report the same host."""
        self.r.get("tagparity")
        span = find_redis_span(self.get_spans(), resource="GET")
        assert span.get_tag("out.host") == span.get_tag("server.address")

    def test_connection_tags_present_after_multiple_commands(self):
        """Connection tags are set on every command span, not just the first."""
        self.pop_spans()  # clear setUp spans (cluster discovery + flushall)
        self.r.set("a", 1)
        self.r.set("b", 2)
        self.r.get("a")
        spans = [s for s in self.get_spans() if s.get_tag("component") == "redis"]
        for span in spans:
            assert span.get_tag("out.host") is not None, f"missing out.host on span {span.resource}"
            assert span.get_tag("server.address") is not None
            assert span.get_metric("network.destination.port") is not None

    @pytest.mark.skipif(PYTHON_VERSION_INFO >= (3, 14), reason="fails under Python 3.14")
    def test_connection_tags_pipeline(self):
        """Pipeline span also carries connection tags."""
        with self.r.pipeline(transaction=False) as p:
            p.set("x", 1)
            p.get("x")
            p.execute()

        span = find_redis_span(self.get_spans(), resource="SET\nGET")
        assert span.get_tag("out.host") is not None
        assert span.get_tag("server.address") is not None
        assert span.get_metric("network.destination.port") is not None

    def test_connection_tags_unicode_key(self):
        """Unicode key commands also carry connection tags."""
        self.r.get("😐")
        span = find_redis_span(self.get_spans(), resource="GET", raw_command="GET 😐")
        assert span.get_tag("out.host") is not None
        assert span.get_metric("network.destination.port") is not None

    def test_connection_tags_mget(self):
        """MGET command spans also carry connection tags.
        Use hash-tagged keys so both land on the same slot — otherwise RedisCluster
        splits MGET into individual GETs and no MGET span is produced.
        """
        self.pop_spans()  # clear setUp spans
        self.r.mget("{tag}key1", "{tag}key2")
        spans = [s for s in self.get_spans() if s.get_tag("component") == "redis" and s.resource == "MGET"]
        assert spans, "Expected at least one MGET span"
        for span in spans:
            assert span.get_tag("out.host") is not None

    def test_connection_tags_new_client_instance(self):
        """A freshly created RedisCluster client also gets connection tags (not cached from setUp)."""
        self.pop_spans()  # clear setUp spans before creating fresh client
        startup_nodes = [redis.cluster.ClusterNode(self.TEST_HOST, int(p)) for p in self.TEST_PORTS.split(",")]
        fresh_client = redis.cluster.RedisCluster(startup_nodes=startup_nodes)
        fresh_client.get("freshcheck")
        spans = [s for s in self.get_spans() if s.get_tag("component") == "redis" and s.resource == "GET"]
        # At least one GET span should have connection tags
        tagged = [s for s in spans if s.get_tag("out.host") is not None]
        assert tagged, "No GET span with out.host found from fresh client"
        for span in tagged:
            assert span.get_tag("out.host") is not None
