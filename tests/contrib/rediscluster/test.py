# -*- coding: utf-8 -*-
import rediscluster

from ddtrace import Pin
from ddtrace.contrib.rediscluster.patch import REDISCLUSTER_VERSION
from ddtrace.contrib.rediscluster.patch import patch
from ddtrace.contrib.rediscluster.patch import unpatch
from tests.contrib.config import REDISCLUSTER_CONFIG
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


class TestRedisPatch(TracerTestCase):

    TEST_SERVICE = "rediscluster-patch"
    TEST_HOST = REDISCLUSTER_CONFIG["host"]
    TEST_PORTS = REDISCLUSTER_CONFIG["ports"]

    def _get_test_client(self):
        startup_nodes = [{"host": self.TEST_HOST, "port": int(port)} for port in self.TEST_PORTS.split(",")]
        if REDISCLUSTER_VERSION >= (2, 0, 0):
            return rediscluster.RedisCluster(startup_nodes=startup_nodes)
        else:
            return rediscluster.StrictRedisCluster(startup_nodes=startup_nodes)

    def setUp(self):
        super(TestRedisPatch, self).setUp()
        patch()
        r = self._get_test_client()
        r.flushall()
        Pin.override(r, service=self.TEST_SERVICE, tracer=self.tracer)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestRedisPatch, self).tearDown()

    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.service == self.TEST_SERVICE
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_tag("redis.raw_command") == u"GET cheese"
        assert span.get_metric("redis.args_length") == 2
        assert span.resource == "GET cheese"

    def test_pipeline(self):
        with self.r.pipeline(transaction=False) as p:
            p.set("blah", 32)
            p.rpush("foo", u"éé")
            p.hgetall("xxx")
            p.execute()

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.service == self.TEST_SERVICE
        assert span.name == "redis.command"
        assert span.resource == u"SET blah 32\nRPUSH foo éé\nHGETALL xxx"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_tag("redis.raw_command") == u"SET blah 32\nRPUSH foo éé\nHGETALL xxx"
        assert span.get_metric("redis.pipeline_length") == 3

    def test_patch_unpatch(self):
        tracer = DummyTracer()

        # Test patch idempotence
        patch()
        patch()

        r = self._get_test_client()
        Pin.get_from(r).clone(tracer=tracer).onto(r)
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
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a user specifies a service for the app
            The rediscluster integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        r = self._get_test_client()
        Pin.get_from(r).clone(tracer=self.tracer).onto(r)
        r.get("key")

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service != "mysvc"
