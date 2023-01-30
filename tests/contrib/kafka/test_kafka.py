# -*- coding: utf-8 -*-

import ddtrace
from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.redis.patch import patch
from ddtrace.contrib.redis.patch import unpatch
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
            "out.port": self.TEST_PORT,
            "out.redis_db": 0,
        }
        for k, v in meta.items():
            assert span.get_tag(k) == v
        for k, v in metrics.items():
            assert span.get_metric(k) == v

        assert span.get_tag("redis.raw_command").startswith(u"MGET 0 1 2 3")
        assert span.get_tag("redis.raw_command").endswith(u"...")
        assert span.get_tag("component") == "redis"
