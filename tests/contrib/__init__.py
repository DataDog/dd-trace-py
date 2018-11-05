"""
TestCases that each integration should inherit.
"""
import wrapt

from tests.utils import delete_module


class PatchMixin(object):
    """
    TestCase for testing the patch logic of an integration.
    """
    def tearDown(self):
        """
        If tests being done require the module to be deleted/unimported a
        helper is provided in tests.utils.delete_module.
        """
        pass

    def delete_module(self, module):
        delete_module(module)

    def assert_wrapped(self, obj):
        """
        Helper to assert that a given obj is properly wrapped by wrapt.
        """
        self.assertTrue(isinstance(obj, wrapt.ObjectProxy), '{} is not wrapped'.format(obj))

    def assert_not_wrapped(self, obj):
        """
        Helper to assert that a given obj is not wrapped by wrapt.
        """
        self.assertFalse(isinstance(obj, wrapt.ObjectProxy), '{} is wrapped'.format(obj))

    def assert_not_double_wrapped(self, obj):
        """
        Helper to assert that a given already wrapped obj is not wrapped twice.

        This is useful for asserting idempotence.
        """
        self.assertTrue(hasattr(obj, '__wrapped__'), '{} is not wrapped'.format(obj))
        self.assert_not_wrapped(obj.__wrapped__)

    def test_patch(self):
        """
        The integration should test that each class, method or function that
        is to be patched is in fact done so.

        For example:

        The redis integration patches the following methods:
        - redis.StrictRedis.execute_command
        - redis.StrictRedis.pipeline
        - redis.Redis.pipeline
        - redis.client.BasePipeline.execute
        - redis.client.BasePipeline.immediate_execute_command

        an appropriate ``test_patch`` would be::

            ddtrace.contrib.redis.patch()
            self.assert_wrapped(redis.StrictRedis.execute_command)
            self.assert_wrapped(redis.StrictRedis.pipeline)
            self.assert_wrapped(redis.Redis.pipeline)
            self.assert_wrapped(redis.client.BasePipeline.execute)
            self.assert_wrapped(redis.client.BasePipeline.immediate_execute_command)
        """
        raise NotImplementedError()

    def test_patch_idempotent(self):
        """
        Proper testing should be done to ensure that multiple calls to the
        integration.patch() method are idempotent. That is, that the
        integration does not patch its library more than once.

        An example for what this might look like is again for the redis
        integration::
            ddtrace.contrib.redis.patch()
            ddtrace.contrib.redis.patch()
            self.assert_not_double_wrapped(redis.StrictRedis.execute_command)
        """
        raise NotImplementedError()
