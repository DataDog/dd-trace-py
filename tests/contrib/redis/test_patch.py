import unittest

from ddtrace.contrib.redis import patch
import wrapt

from ..contrib import PatchTestCase


class TestRedisPatch(unittest.TestCase, PatchTestCase):
    def test_patch(self):
        """
        """
        patch()

        import redis
        assert hasattr(redis, '_datadog_patch')
        assert getattr(redis, '_datadog_patch')

        assert isinstance(redis.StrictRedis.execute_command, wrapt.ObjectProxy)
        assert isinstance(redis.StrictRedis.pipeline, wrapt.ObjectProxy)
        assert isinstance(redis.Redis.pipeline, wrapt.ObjectProxy)
        assert isinstance(redis.client.BasePipeline.execute, wrapt.ObjectProxy)
        assert isinstance(redis.client.BasePipeline.immediate_execute_command, wrapt.ObjectProxy)

    def test_patch_idempotent(self):
        patch()
        patch()

        import redis
        assert not isinstance(redis.StrictRedis.execute_command.__wrapped__, wrapt.ObjectProxy)
