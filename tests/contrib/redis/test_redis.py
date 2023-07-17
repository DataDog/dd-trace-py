# -*- coding: utf-8 -*-
import redis

import ddtrace
from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.redis.patch import patch
from ddtrace.contrib.redis.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.opentracer.utils import init_tracer
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import snapshot

from ..config import REDIS_CONFIG


class TestRedisPatch(TracerTestCase):

    TEST_PORT = REDIS_CONFIG["port"]

    def setUp(self):
        super(TestRedisPatch, self).setUp()
        patch()
        r = redis.Redis(port=self.TEST_PORT)
        r.flushall()
        Pin.override(r, tracer=self.tracer)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestRedisPatch, self).tearDown()

    def test_long_command(self):
        self.r.mget(*range(1000))

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        self.assert_is_measured(span)
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        meta = {
            "out.host": u"localhost",
        }
        metrics = {
            "network.destination.port": self.TEST_PORT,
            "out.redis_db": 0,
        }
        for k, v in meta.items():
            assert span.get_tag(k) == v
        for k, v in metrics.items():
            assert span.get_metric(k) == v

        assert span.get_tag("redis.raw_command").startswith(u"MGET 0 1 2 3")
        assert span.get_tag("redis.raw_command").endswith(u"...")
        assert span.get_tag("component") == "redis"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "redis"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_service_name_v1(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_operation_name_v0_schema(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        span = spans[0]
        assert span.name == "redis.command"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_operation_name_v1_schema(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        span = spans[0]
        assert span.name == "redis.command"

    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        self.assert_is_measured(span)
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_metric("out.redis_db") == 0
        assert span.get_tag("out.host") == "localhost"
        assert span.get_tag("redis.raw_command") == u"GET cheese"
        assert span.get_tag("component") == "redis"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "redis"
        assert span.get_metric("redis.args_length") == 2
        assert span.resource == "GET cheese"
        assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    def test_analytics_without_rate(self):
        with self.override_config("redis", dict(analytics_enabled=True)):
            us = self.r.get("cheese")
            assert us is None
            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]
            assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0

    def test_analytics_with_rate(self):
        with self.override_config("redis", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            us = self.r.get("cheese")
            assert us is None
            spans = self.get_spans()
            assert len(spans) == 1
            span = spans[0]
            assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5

    def test_pipeline_traced(self):
        with self.r.pipeline(transaction=False) as p:
            p.set("blah", 32)
            p.rpush("foo", u"éé")
            p.hgetall("xxx")
            p.execute()

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        self.assert_is_measured(span)
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.resource == u"SET blah 32\nRPUSH foo éé\nHGETALL xxx"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_metric("out.redis_db") == 0
        assert span.get_tag("out.host") == "localhost"
        assert span.get_tag("redis.raw_command") == u"SET blah 32\nRPUSH foo éé\nHGETALL xxx"
        assert span.get_tag("component") == "redis"
        assert span.get_tag("span.kind") == "client"
        assert span.get_metric("redis.pipeline_length") == 3
        assert span.get_metric("redis.pipeline_length") == 3
        assert span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    def test_pipeline_immediate(self):
        with self.r.pipeline() as p:
            p.set("a", 1)
            p.immediate_execute_command("SET", "a", 1)
            p.execute()

        spans = self.get_spans()
        assert len(spans) == 2
        span = spans[0]
        self.assert_is_measured(span)
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.resource == u"SET a 1"
        assert span.span_type == "redis"
        assert span.error == 0
        assert span.get_metric("out.redis_db") == 0
        assert span.get_tag("out.host") == "localhost"
        assert span.get_tag("component") == "redis"
        assert span.get_tag("span.kind") == "client"

    def test_meta_override(self):
        r = self.r
        pin = Pin.get_from(r)
        if pin:
            pin.clone(tags={"cheese": "camembert"}).onto(r)

        r.get("cheese")
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "redis"
        assert "cheese" in span.get_tags() and span.get_tag("cheese") == "camembert"

    def test_patch_unpatch(self):
        tracer = DummyTracer()

        # Test patch idempotence
        patch()
        patch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        r.get("key")

        spans = tracer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

    def test_opentracing(self):
        """Ensure OpenTracing works with redis."""
        ot_tracer = init_tracer("redis_svc", self.tracer)

        with ot_tracer.start_active_span("redis_get"):
            us = self.r.get("cheese")
            assert us is None

        spans = self.get_spans()
        assert len(spans) == 2
        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.name == "redis_get"
        assert ot_span.service == "redis_svc"

        self.assert_is_measured(dd_span)
        assert dd_span.service == "redis"
        assert dd_span.name == "redis.command"
        assert dd_span.span_type == "redis"
        assert dd_span.error == 0
        assert dd_span.get_metric("out.redis_db") == 0
        assert dd_span.get_tag("out.host") == "localhost"
        assert dd_span.get_tag("redis.raw_command") == u"GET cheese"
        assert dd_span.get_tag("component") == "redis"
        assert dd_span.get_tag("span.kind") == "client"
        assert dd_span.get_tag("db.system") == "redis"
        assert dd_span.get_metric("redis.args_length") == 2
        assert dd_span.resource == "GET cheese"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_default(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "redis"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "redis"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_REDIS_SERVICE="myredis", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_env_user_specified_redis_service_v0(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myredis", span.service

        self.reset()

        # Global config
        with self.override_config("redis", dict(service="cfg-redis")):
            self.r.get("cheese")
            span = self.get_spans()[0]
            assert span.service == "cfg-redis", span.service

        self.reset()

        # Manual override
        Pin.override(self.r, service="mysvc", tracer=self.tracer)
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "mysvc", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_REDIS_SERVICE="myredis", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_env_user_specified_redis_service_v1(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myredis", span.service

        self.reset()

        # Global config
        with self.override_config("redis", dict(service="cfg-redis")):
            self.r.get("cheese")
            span = self.get_spans()[0]
            assert span.service == "cfg-redis", span.service

        self.reset()

        # Manual override
        Pin.override(self.r, service="mysvc", tracer=self.tracer)
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "mysvc", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_SERVICE="app-svc", DD_REDIS_SERVICE="env-specified-redis-svc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"
        )
    )
    def test_service_precedence_v0(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "env-specified-redis-svc", span.service

        self.reset()

        # Do a manual override
        Pin.override(self.r, service="override-redis", tracer=self.tracer)
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "override-redis", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_SERVICE="app-svc", DD_REDIS_SERVICE="env-specified-redis-svc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"
        )
    )
    def test_service_precedence_v1(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "env-specified-redis-svc", span.service

        self.reset()

        # Do a manual override
        Pin.override(self.r, service="override-redis", tracer=self.tracer)
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "override-redis", span.service


class TestRedisPatchSnapshot(TracerTestCase):

    TEST_PORT = REDIS_CONFIG["port"]

    def setUp(self):
        super(TestRedisPatchSnapshot, self).setUp()
        patch()
        r = redis.Redis(port=self.TEST_PORT)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestRedisPatchSnapshot, self).tearDown()
        self.r.flushall()

    @snapshot()
    def test_long_command(self):
        self.r.mget(*range(1000))

    @snapshot()
    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None

    @snapshot()
    def test_unicode(self):
        us = self.r.get(u"😐")
        assert us is None

    @snapshot()
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
        tracer = DummyTracer()

        # Test patch idempotence
        patch()
        patch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        r.get("key")

        spans = tracer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = redis.Redis(port=REDIS_CONFIG["port"])
        Pin.get_from(r).clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

    @snapshot()
    def test_opentracing(self):
        """Ensure OpenTracing works with redis."""
        writer = ddtrace.tracer._writer
        ot_tracer = init_tracer("redis_svc", ddtrace.tracer)
        # FIXME: OpenTracing always overrides the hostname/port and creates a new
        #        writer so we have to reconfigure with the previous one
        ddtrace.tracer.configure(writer=writer)

        with ot_tracer.start_active_span("redis_get"):
            us = self.r.get("cheese")
            assert us is None

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    @snapshot()
    def test_user_specified_service(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_REDIS_SERVICE="myredis"))
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

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="app-svc", DD_REDIS_SERVICE="env-redis"))
    @snapshot()
    def test_service_precedence(self):
        self.r.get("cheese")

        self.reset()

        # Do a manual override
        Pin.override(self.r, service="override-redis", tracer=self.tracer)
        self.r.get("cheese")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_REDIS_CMD_MAX_LENGTH="10"))
    @snapshot()
    def test_custom_cmd_length_env(self):
        self.r.get("here-is-a-long-key-name")

    @snapshot()
    def test_custom_cmd_length(self):
        with self.override_config("redis", dict(cmd_max_length=7)):
            self.r.get("here-is-a-long-key-name")
