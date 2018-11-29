import importlib
import sys
import unittest

import wrapt

from ddtrace import patch

from tests.subprocesstest import SubprocessTestCase, run_in_subprocess


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


def require_modules(f):
    """
    A helper for PatchTestCase test methods that will check if there are any
    modules to use else raise a NotImplementedError.

    :param f: method to wrap with a check
    """
    def checked_method(self, *args, **kwargs):
        if not self.MODULES:
            raise NotImplementedError(f.__doc__)
        return f(self, *args, **kwargs)
    return checked_method


class PatchTestCase(object):
    """
    unittest or other test runners will pick up the base test case as a testcase
    since it inherits from unittest.TestCase unless we wrap it with this empty
    parent class.
    """
    @run_in_subprocess
    class Base(SubprocessTestCase, PatchMixin):
        """PatchTestCase provides default test methods to be used for testing
        common patching logic. Each test method provides a default
        implementation which will use `self.MODULES` if it exists.

        If `self.MODULES` is not provided and the test method is not overridden,
        a NotImplementedError will be raised encouraging the user to implement
        the test.

        Attributes:
            MODULES: A list of tuples of the form
                    (module_name, integration_name, unpatch_method)

        Example:

        A simple implementation inheriting this TestCase looks like::

            from ddtrace.contrib.redis import unpatch

            class RedisPatchTestCase(PatchTestCase.Base):
                # 'redis' module, 'redis' integration, redis unpatch method
                MODULES = [('redis', 'redis', unpatch)]

                def assert_patched(self, redis):
                    # assert patching logic
                    # self.assert_wrapped(...)

                def assert_not_patched(self, redis):
                    # assert patching logic
                    # self.assert_not_wrapped(...)

                def assert_not_double_patched(self, redis):
                    # assert patching logic
                    # self.assert_not_double_wrapped(...)

                def test_patch_before_import(self):
                    # defer to parent test case logic
                    super(RedisPatchTestCase, self).test_patch_before_import()

                # implement the rest of the methods...
        """
        MODULES = []

        def assert_patched(self, module):
            """
            Asserts that the given module is patched.

            For example, the redis integration patches the following methods:
                - redis.StrictRedis.execute_command
                - redis.StrictRedis.pipeline
                - redis.Redis.pipeline
                - redis.client.BasePipeline.execute
                - redis.client.BasePipeline.immediate_execute_command

            So an appropriate assert_patched would look like::

            def assert_patched(self, redis):
                self.assert_wrapped(redis.StrictRedis.execute_command)
                self.assert_wrapped(redis.StrictRedis.pipeline)
                self.assert_wrapped(redis.Redis.pipeline)
                self.assert_wrapped(redis.client.BasePipeline.execute)
                self.assert_wrapped(redis.client.BasePipeline.immediate_execute_command)

            :param module: module to check
            :return: None
            """
            raise NotImplementedError(self.assert_patched.__doc__)

        def assert_not_patched(self, module):
            """
            Asserts that the given module is not patched.

            For example, the redis integration patches the following methods:
                - redis.StrictRedis.execute_command
                - redis.StrictRedis.pipeline
                - redis.Redis.pipeline
                - redis.client.BasePipeline.execute
                - redis.client.BasePipeline.immediate_execute_command

            So an appropriate assert_not_patched would look like::

            def assert_not_patched(self, redis):
                self.assert_not_wrapped(redis.StrictRedis.execute_command)
                self.assert_not_wrapped(redis.StrictRedis.pipeline)
                self.assert_not_wrapped(redis.Redis.pipeline)
                self.assert_not_wrapped(redis.client.BasePipeline.execute)
                self.assert_not_wrapped(redis.client.BasePipeline.immediate_execute_command)

            :param module:
            :return: None
            """
            raise NotImplementedError(self.assert_not_patched.__doc__)

        def assert_not_double_patched(self, module):
            """
            Asserts that the given module is not patched twice.

            For example, the redis integration patches the following methods:
                - redis.StrictRedis.execute_command
                - redis.StrictRedis.pipeline
                - redis.Redis.pipeline
                - redis.client.BasePipeline.execute
                - redis.client.BasePipeline.immediate_execute_command

            So an appropriate assert_not_double_patched would look like::

            def assert_not_double_patched(self, redis):
                self.assert_not_double_wrapped(redis.StrictRedis.execute_command)
                self.assert_not_double_wrapped(redis.StrictRedis.pipeline)
                self.assert_not_double_wrapped(redis.Redis.pipeline)
                self.assert_not_double_wrapped(redis.client.BasePipeline.execute)
                self.assert_not_double_wrapped(redis.client.BasePipeline.immediate_execute_command)

            :param module: module to check
            :return: None
            """
            raise NotImplementedError(self.assert_not_double_patched.__doc__)

        @require_modules
        def test_patch_before_import(self):
            """
            The integration should test that each class, method or function that
            is to be patched is in fact done so when ddtrace.patch() is called
            before the module is imported.

            For example:

            an appropriate ``test_patch_before_import`` would be::

                ddtrace.patch(redis=True)
                import redis
                self.assert_patched(redis)
            """
            for module_name, integration_name, _ in self.MODULES:
                self.assert_module_not_imported(module_name)
                patch(**{integration_name: True})
                module = importlib.import_module(module_name)
                self.assert_patched(module)

        @require_modules
        def test_patch_after_import(self):
            """
            The integration should test that each class, method or function that
            is to be patched is in fact done so when ddtrace.patch() is called
            after the module is imported.

            an appropriate ``test_patch_after_import`` would be::

                import redis
                ddtrace.patch(redis=True)
                self.assert_patched(redis)
            """
            for module_name, integration_name, _ in self.MODULES:
                self.assert_module_not_imported(module_name)
                module = importlib.import_module(module_name)
                patch(**{integration_name: True})
                self.assert_patched(module)

        @require_modules
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
            for module_name, integration_name, _ in self.MODULES:
                self.assert_module_not_imported(module_name)
                # Patch the module twice.
                patch(**{module_name: True})
                patch(**{module_name: True})
                module = importlib.import_module(module_name)
                self.assert_patched(module)
                self.assert_not_double_patched(module)

        @require_modules
        def test_patch_unpatch_patch(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it and then subsequently
            patch it again.

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
            for module_name, integration_name, unpatch in self.MODULES:
                self.assert_module_not_imported(module_name)
                patch(**{integration_name: True})
                unpatch()
                patch(**{integration_name: True})
                module = importlib.import_module(module_name)
                self.assert_patched(module)

        @require_modules
        def test_unpatch_before_import(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it before importing the
            library.

            For example::

                ddtrace.patch(redis=True)
                from ddtrace.contrib.redis import unpatch
                unpatch()
                import redis
                self.assert_not_patched(redis)
            """
            for module_name, integration_name, unpatch in self.MODULES:
                self.assert_module_not_imported(module_name)
                patch(**{integration_name: True})
                unpatch()
                module = importlib.import_module(module_name)
                self.assert_not_patched(module)

        @require_modules
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
            for module_name, integration_name, unpatch in self.MODULES:
                self.assert_module_not_imported(module_name)
                patch(**{integration_name: True})
                module = importlib.import_module(module_name)
                unpatch()
                self.assert_not_patched(module)

        @require_modules
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
            for module_name, integration_name, unpatch in self.MODULES:
                self.assert_module_not_imported(module_name)
                unpatch()
                unpatch()
                module = importlib.import_module(module_name)
                self.assert_not_patched(module)
