# -*- coding: utf-8 -*-
import pytest
import rediscluster

from ddtrace.contrib.internal.rediscluster.patch import REDISCLUSTER_VERSION
from ddtrace.contrib.internal.rediscluster.patch import patch
from ddtrace.contrib.internal.rediscluster.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.trace import Pin
from tests.contrib.config import REDISCLUSTER_CONFIG
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import override_config


@pytest.fixture()
def redis_client():
    patch()
    try:
        r = _get_test_client()
        r.flushall()
        yield r
    finally:
        unpatch()


def _get_test_client():
    # type: () -> rediscluster.StrictRedisCluster
    host = REDISCLUSTER_CONFIG["host"]
    ports = REDISCLUSTER_CONFIG["ports"]

    startup_nodes = [{"host": host, "port": int(port)} for port in ports.split(",")]
    if REDISCLUSTER_VERSION >= (2, 0, 0):
        return rediscluster.RedisCluster(startup_nodes=startup_nodes)
    else:
        return rediscluster.StrictRedisCluster(startup_nodes=startup_nodes)


class TestGrokzenRedisClusterPatch(TracerTestCase):
    def setUp(self):
        super(TestGrokzenRedisClusterPatch, self).setUp()
        patch()
        r = _get_test_client()
        r.flushall()
        Pin._override(r, tracer=self.tracer)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestGrokzenRedisClusterPatch, self).tearDown()

    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.service == "rediscluster"
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_tag("redis.raw_command") == "GET cheese"
        assert span.get_tag("component") == "rediscluster"
        assert span.get_tag("span.kind") == "client"
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
        assert span.service == "rediscluster"
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_tag("redis.raw_command") == "GET 😐"
        assert span.get_tag("component") == "rediscluster"
        assert span.get_tag("span.kind") == "client"
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
        assert span.service == "rediscluster"
        assert span.name == "redis.command"
        assert span.resource == "SET blah 32\nRPUSH foo éé\nHGETALL xxx"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_tag("redis.raw_command") == "SET blah 32\nRPUSH foo éé\nHGETALL xxx"
        assert span.get_tag("component") == "rediscluster"
        assert span.get_tag("span.kind") == "client"
        assert span.get_metric("redis.pipeline_length") == 3

    def test_patch_unpatch(self):
        tracer = DummyTracer()

        # Test patch idempotence
        patch()
        patch()

        r = _get_test_client()
        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        r = _get_test_client()
        r.get("key")

        spans = tracer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = _get_test_client()
        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The rediscluster integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        r = _get_test_client()
        Pin.get_from(r)._clone(tracer=self.tracer).onto(r)
        r.get("key")

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The rediscluster integration should use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        r = _get_test_client()
        Pin.get_from(r)._clone(tracer=self.tracer).onto(r)
        r.get("key")

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc", span.service

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1(self):
        """
        v1: When a service isn't specified, we should end up with
            the default span service name
        """
        r = _get_test_client()
        Pin.get_from(r)._clone(tracer=self.tracer).onto(r)
        r.get("key")

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_REDISCLUSTER_SERVICE="myrediscluster"))
    def test_env_user_specified_rediscluster_service(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myrediscluster", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SERVICE="app-svc", DD_REDISCLUSTER_SERVICE="myrediscluster")
    )
    def test_service_precedence(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myrediscluster"

        self.reset()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_span_name_v0_schema(self):
        """
        v0: When a service isn't specified, we should end up with
            the default span service name
        """
        r = _get_test_client()
        Pin.get_from(r)._clone(tracer=self.tracer).onto(r)
        r.get("key")

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "redis.command"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_span_name_v1_schema(self):
        """
        v1: When a service isn't specified, we should end up with
            the default span service name
        """
        r = _get_test_client()
        Pin.get_from(r)._clone(tracer=self.tracer).onto(r)
        r.get("key")

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "redis.command"


@pytest.mark.snapshot
def test_cmd_max_length(redis_client):
    with override_config("rediscluster", dict(cmd_max_length=7)):
        redis_client.get("here-is-a-long-key")


@pytest.mark.skip(reason="No traces sent to the test agent")
@pytest.mark.subprocess(env=dict(DD_REDISCLUSTER_CMD_MAX_LENGTH="10"), ddtrace_run=True)
@pytest.mark.snapshot
def test_cmd_max_length_env():
    from tests.contrib.rediscluster.test import _get_test_client

    r = _get_test_client()
    r.get("here-is-a-long-key")


@pytest.mark.subprocess(env=dict(DD_REDIS_RESOURCE_ONLY_COMMAND="false", DD_TRACE_REDIS_ENABLED="0"))
@pytest.mark.snapshot
def test_full_command_in_resource_env():
    import ddtrace.auto  # noqa

    import ddtrace
    from tests.contrib.rediscluster.test import _get_test_client

    ddtrace.patch(rediscluster=True)

    with ddtrace.tracer.trace("web-request", service="test"):
        redis_client = _get_test_client()
        redis_client.get("put_key_in_resource")
        p = redis_client.pipeline(transaction=False)
        p.set("pipeline-cmd1", 1)
        p.set("pipeline-cmd2", 2)
        p.execute()


@pytest.mark.snapshot
@pytest.mark.parametrize("use_global_tracer", [True])
def test_full_command_in_resource_config(tracer, redis_client):
    with override_config("rediscluster", dict(resource_only_command=False)):
        with tracer.trace("web-request", service="test"):
            redis_client.get("put_key_in_resource")
            p = redis_client.pipeline(transaction=False)
            p.set("pipeline-cmd1", 1)
            p.set("pipeline-cmd2", 2)
            p.execute()
