"""
TestCases that each integration should inherit.
"""
import sys

import wrapt


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

    def reload_module(self, module):
        """
        Reloads a module using the appropriate Python library function.
        """
        from ddtrace.compat import PY2
        if PY2:
            import imp
            reload = imp.reload
        else:
            import importlib
            reload = importlib.reload

        reload(module)

    def module_imported(self, modname):
        """Returns whether the module, given its name is imported."""
        return modname in sys.modules

    def assert_wrapped(self, obj):
        """
        Helper to assert that a given object is properly wrapped by wrapt.
        """
        self.assertTrue(isinstance(obj, wrapt.ObjectProxy), '{} is not wrapped'.format(obj))

    def assert_not_wrapped(self, obj):
        """
        Helper to assert that a given object is not wrapped by wrapt.
        """
        self.assertFalse(isinstance(obj, wrapt.ObjectProxy), '{} is wrapped'.format(obj))

    def assert_not_double_wrapped(self, obj):
        """
        Helper to assert that a given already wrapped object is not wrapped twice.

        This is useful for asserting idempotence.
        """
        self.assertTrue(hasattr(obj, '__wrapped__'), '{} is not wrapped'.format(obj))
        self.assert_not_wrapped(obj.__wrapped__)

    def test_patched_library_not_imported(self):
        """
        TODO: this would be great to test, however we do not have a reliable
        mechanism to "unimport" modules between each test case.
        """
        pass
        # raise NotImplementedError()

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

            # If the module has been previously loaded then take note of it.
            # We have to reload the module if it has already been loaded to ensure
            # the patching works.
            trigger_reload = self.module_imported('redis')
            ddtrace.patch(redis=True)
            import redis
            if trigger_reload:
                self.reload_module(redis)
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
        """
        raise NotImplementedError(self.test_unpatch_before_import.__doc__)

    def test_unpatch_after_import(self):
        """
        To ensure that we can thoroughly test the installation/patching of an
        integration we must be able to unpatch it after importing the library.
        """
        raise NotImplementedError(self.test_unpatch_after_import.__doc__)
