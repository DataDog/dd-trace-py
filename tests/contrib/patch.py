import unittest
import sys

import wrapt


class PatchMixin(unittest.TestCase):
    """
    TestCase for testing the patch logic of an integration.
    """
    def module_imported(self, modname):
        """
        Returns whether a module is imported or not.
        """
        return modname in sys.modules

    def assert_module_imported(self, modname):
        """
        Asserts that the module, given its name is imported.
        """
        assert self.module_imported(modname), '{} module not imported'.format(modname)

    def assert_module_not_imported(self, modname):
        """
        Asserts that the module, given its name is not imported.
        """
        assert not self.module_imported(modname), '{} module is imported'.format(modname)

    def is_wrapped(self, obj):
        return isinstance(obj, wrapt.ObjectProxy)

    def assert_wrapped(self, obj):
        """
        Helper to assert that a given object is properly wrapped by wrapt.
        """
        self.assertTrue(self.is_wrapped(obj), '{} is not wrapped'.format(obj))

    def assert_not_wrapped(self, obj):
        """
        Helper to assert that a given object is not wrapped by wrapt.
        """
        self.assertFalse(self.is_wrapped(obj), '{} is wrapped'.format(obj))

    def assert_not_double_wrapped(self, obj):
        """
        Helper to assert that a given already wrapped object is not wrapped twice.

        This is useful for asserting idempotence.
        """
        self.assert_wrapped(obj)
        self.assert_not_wrapped(obj.__wrapped__)


class PatchTestCase(PatchMixin):
    def test_patch_before_import(self):
        """
        The integration should test that each class, method or function that
        is to be patched is in fact done so when ddtrace.patch() is called
        before the module is imported.

        For example:

        The redis integration patches the following methods:
        - redis.StrictRedis.execute_command
        - redis.StrictRedis.pipeline
        - redis.Redis.pipeline
        - redis.client.BasePipeline.execute
        - redis.client.BasePipeline.immediate_execute_command

        an appropriate ``test_patch_before_import`` would be::

            ddtrace.patch(redis=True)
            import redis
            self.assert_wrapped(redis.StrictRedis.execute_command)
            self.assert_wrapped(redis.StrictRedis.pipeline)
            self.assert_wrapped(redis.Redis.pipeline)
            self.assert_wrapped(redis.client.BasePipeline.execute)
            self.assert_wrapped(redis.client.BasePipeline.immediate_execute_command)
        """
        raise NotImplementedError(self.test_patch_before_import.__doc__)

    def test_patch_after_import(self):
        """
        The integration should test that each class, method or function that
        is to be patched is in fact done so when ddtrace.patch() is called
        after the module is imported.

        For example:

        The redis integration patches the following methods:
        - redis.StrictRedis.execute_command
        - redis.StrictRedis.pipeline
        - redis.Redis.pipeline
        - redis.client.BasePipeline.execute
        - redis.client.BasePipeline.immediate_execute_command

        an appropriate ``test_patch_after_import`` would be::

            import redis
            ddtrace.patch(redis=True)
            self.assert_wrapped(redis.StrictRedis.execute_command)
            self.assert_wrapped(redis.StrictRedis.pipeline)
            self.assert_wrapped(redis.Redis.pipeline)
            self.assert_wrapped(redis.client.BasePipeline.execute)
            self.assert_wrapped(redis.client.BasePipeline.immediate_execute_command)
        """
        raise NotImplementedError(self.test_patch_after_import.__doc__)

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
        raise NotImplementedError(self.test_patch_idempotent.__doc__)

    def test_unpatch_before_import(self):
        """
        To ensure that we can thoroughly test the installation/patching of an
        integration we must be able to unpatch it before importing the library.

        For example::

            ddtrace.patch(redis=True)
            from ddtrace.contrib.redis import unpatch
            unpatch()
            import redis
            self.assert_not_wrapped(redis.StrictRedis.execute_command)
            self.assert_not_wrapped(redis.StrictRedis.pipeline)
            self.assert_not_wrapped(redis.Redis.pipeline)
            self.assert_not_wrapped(redis.client.BasePipeline.execute)
            self.assert_not_wrapped(redis.client.BasePipeline.immediate_execute_command)
        """
        raise NotImplementedError(self.test_unpatch_before_import.__doc__)

    def test_unpatch_after_import(self):
        """
        To ensure that we can thoroughly test the installation/patching of an
        integration we must be able to unpatch it after importing the library.

        For example::

            import redis
            from ddtrace.contrib.redis import unpatch
            ddtrace.patch(redis=True)
            unpatch()
            self.assert_not_wrapped(redis.StrictRedis.execute_command)
            self.assert_not_wrapped(redis.StrictRedis.pipeline)
            self.assert_not_wrapped(redis.Redis.pipeline)
            self.assert_not_wrapped(redis.client.BasePipeline.execute)
            self.assert_not_wrapped(redis.client.BasePipeline.immediate_execute_command)
        """
        raise NotImplementedError(self.test_unpatch_after_import.__doc__)

    def test_unpatch_patch(self):
        """
        To ensure that we can thoroughly test the installation/patching of an
        integration we must be able to unpatch it and then subsequently patch it
        again.

        For example::

            import redis
            from ddtrace.contrib.redis import unpatch

            ddtrace.patch(redis=True)
            unpatch()
            ddtrace.patch(redis=True)
            self.assert_wrapped(redis.StrictRedis.execute_command)
            self.assert_wrapped(redis.StrictRedis.pipeline)
            self.assert_wrapped(redis.Redis.pipeline)
            self.assert_wrapped(redis.client.BasePipeline.execute)
            self.assert_wrapped(redis.client.BasePipeline.immediate_execute_command)
        """
        raise NotImplementedError(self.test_unpatch_patch.__doc__)

    def test_unpatch_idempotent(self):
        """
        Unpatching twice should be a no-op.

        For example::

            import redis
            from ddtrace.contrib.redis import unpatch

            ddtrace.patch(redis=True)
            unpatch()
            unpatch()
            self.assert_not_wrapped(redis.StrictRedis.execute_command)
            self.assert_not_wrapped(redis.StrictRedis.pipeline)
            self.assert_not_wrapped(redis.Redis.pipeline)
            self.assert_not_wrapped(redis.client.BasePipeline.execute)
            self.assert_not_wrapped(redis.client.BasePipeline.immediate_execute_command)
        """
        raise NotImplementedError(self.test_unpatch_idempotent.__doc__)
