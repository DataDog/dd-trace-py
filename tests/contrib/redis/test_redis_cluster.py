import pytest
import redis

from ddtrace import Pin
from ddtrace.contrib.redis.patch import patch
from ddtrace.contrib.redis.patch import unpatch
from tests.contrib.rediscluster.test import TestGrokzenRedisClusterPatch


@pytest.skip(redis.VERSION < (4, 1), "redis.cluster is not implemented in redis<4.1")
class TestRedisClusterPatch(TestGrokzenRedisClusterPatch):
    def _get_test_client(self):
        startup_nodes = [{"host": self.TEST_HOST, "port": int(port)} for port in self.TEST_PORTS.split(",")]
        return redis.cluster.RedisCluster(startup_nodes=startup_nodes)

    def setUp(self):
        super(TestRedisClusterPatch, self).setUp()
        patch()
        r = self._get_test_client()
        r.flushall()
        Pin.override(r, tracer=self.tracer)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestRedisClusterPatch, self).tearDown()
