# -*- coding: utf-8 -*-
import redis

import ddtrace
from ddtrace import Pin, compat
from ddtrace.contrib.redis import get_traced_redis
from ddtrace.contrib.redis.patch import patch, unpatch

from tests.opentracer.utils import init_tracer
from tests.util import snapshot
from ..config import REDIS_CONFIG
from ...test_tracer import get_dummy_tracer
from ...base import BaseTracerTestCase


def test_redis_legacy():
    # ensure the old interface isn't broken, but doesn't trace
    tracer = get_dummy_tracer()
    TracedRedisCache = get_traced_redis(tracer, "foo")
    r = TracedRedisCache(port=REDIS_CONFIG["port"])
    r.set("a", "b")
    got = r.get("a")
    assert compat.to_unicode(got) == "b"
    assert not tracer.writer.pop()


class TestRedisPatch(BaseTracerTestCase):

    TEST_PORT = REDIS_CONFIG["port"]

    def setUp(self):
        super(TestRedisPatch, self).setUp()
        patch()
        r = redis.Redis(port=self.TEST_PORT)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestRedisPatch, self).tearDown()
        self.r.flushall()

    @snapshot()
    def test_long_command(self):
        self.r.mget(*range(1000))

    @snapshot()
    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None

    def test_analytics_without_rate(self):
        with self.override_config("redis", dict(analytics_enabled=True)):
            us = self.r.get("cheese")
            assert us is None

    @snapshot()
    def test_analytics_with_rate(self):
        with self.override_config("redis", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            us = self.r.get("cheese")
            assert us is None

    @snapshot()
    def test_pipeline_traced(self):
        with self.r.pipeline(transaction=False) as p:
            p.set("blah", 32)
            p.rpush("foo", u"éé")
            p.hgetall("xxx")
            p.execute()

    @snapshot()
    def test_pipeline_immediate(self):
        with self.r.pipeline() as p:
            p.set("a", 1)
            p.immediate_execute_command("SET", "a", 1)
            p.execute()

    @snapshot()
    def test_meta_override(self):
        r = self.r
        pin = Pin.get_from(r)
        if pin:
            pin.clone(tags={"cheese": "camembert"}).onto(r)

        r.get("cheese")

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get("key")

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        r.get("key")

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get("key")

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 1

    @snapshot()
    def test_opentracing(self):
        """Ensure OpenTracing works with redis."""
        ot_tracer = init_tracer("redis_svc", ddtrace.tracer)

        with ot_tracer.start_active_span("redis_get"):
            us = self.r.get("cheese")
            assert us is None

    @BaseTracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    @snapshot()
    def test_user_specified_service(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")

    @BaseTracerTestCase.run_in_subprocess(env_overrides=dict(DD_REDIS_SERVICE="myredis"))
    @snapshot()
    def test_env_user_specified_redis_service(self):
        self.r.get("cheese")

        self.reset()

        # Global config
        with self.override_config("redis", dict(service="cfg-redis")):
            self.r.get("cheese")

        self.reset()

        # Manual override
        Pin.override(self.r, service="mysvc", tracer=self.tracer)
        self.r.get("cheese")

    @snapshot()
    @BaseTracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="app-svc", DD_REDIS_SERVICE="env-redis"))
    def test_service_precedence(self):
        self.r.get("cheese")

        self.reset()

        # Do a manual override
        Pin.override(self.r, service="override-redis", tracer=self.tracer)
        self.r.get("cheese")
