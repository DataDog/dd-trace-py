import functools
import http.client as httplib
import importlib
import json
import os
import sys
from tempfile import NamedTemporaryFile
from textwrap import dedent
import unittest

import wrapt

from ddtrace.version import get_version
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess
from tests.utils import call_program


TRACER_VERSION = get_version()


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
        assert self.module_imported(modname), "{} module not imported".format(modname)

    def assert_not_module_imported(self, modname):
        """
        Asserts that the module, given its name is not imported.
        """
        assert not self.module_imported(modname), "{} module is imported".format(modname)

    def is_wrapped(self, obj):
        return isinstance(obj, wrapt.ObjectProxy) or hasattr(obj, "__dd_wrapped__")

    def assert_wrapped(self, obj):
        """
        Helper to assert that a given object is properly wrapped by wrapt.
        """
        self.assertTrue(self.is_wrapped(obj), "{} is not wrapped".format(obj))

    def assert_not_wrapped(self, obj):
        """
        Helper to assert that a given object is not wrapped by wrapt.
        """
        self.assertFalse(self.is_wrapped(obj), "{} is wrapped".format(obj))

    def assert_not_double_wrapped(self, obj):
        """
        Helper to assert that a given already wrapped object is not wrapped twice.

        This is useful for asserting idempotence.
        """
        self.assert_wrapped(obj)

        wrapped = obj.__wrapped__ if isinstance(obj, wrapt.ObjectProxy) else obj.__dd_wrapped__

        self.assert_not_wrapped(wrapped)


def raise_if_no_attrs(f):
    """
    A helper for PatchTestCase test methods that will check if there are any
    modules to use else raise a NotImplementedError.

    :param f: method to wrap with a check
    """
    required_attrs = [
        "__module_name__",
        "__integration_name__",
        "__patch_func__",
    ]

    @functools.wraps(f)
    def checked_method(self, *args, **kwargs):
        for attr in required_attrs:
            if not getattr(self, attr):
                raise NotImplementedError(f.__doc__)
        return f(self, *args, **kwargs)

    return checked_method


def noop_if_no_unpatch(f):
    """
    A helper for PatchTestCase test methods that will no-op the test if the
    __unpatch_func__ attribute is None
    """

    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        if self.__unpatch_func__ is None:
            self.skipTest(reason="No unpatch function given")
        return f(self, *args, **kwargs)

    return wrapper


def emit_integration_and_version_to_test_agent(integration_name, version, module_name=None):
    # Define the data payload
    data = {
        "integration_name": integration_name,
        "integration_version": version,
        "tracer_version": TRACER_VERSION,
        "tracer_language": "python",
        # we want the toplevel module name: ie for snowflake.connector, we want snowflake
        "dependency_name": module_name.split(".")[0] if module_name else integration_name,
    }
    payload = json.dumps(data)
    conn = httplib.HTTPConnection(host="localhost", port=9126, timeout=10)
    headers = {"Content-type": "application/json"}
    conn.request("PUT", "/test/session/integrations", body=payload, headers=headers)
    response = conn.getresponse()
    assert response.status == 200


class PatchTestCase(object):
    """
    unittest or other test runners will pick up the base test case as a testcase
    since it inherits from unittest.TestCase unless we wrap it with this empty
    parent class.
    """

    @run_in_subprocess
    class Base(SubprocessTestCase, PatchMixin):
        """Provides default test methods to be used for testing common integration patching logic.
        Each test method provides a default implementation which will use the
        provided attributes (described below). If the attributes are not
        provided a NotImplementedError will be raised for each method that is
        not overridden.

        Attributes:
            __integration_name__ the name of the integration.
            __module_name__ module which the integration patches.
            __patch_func__ patch function from the integration.
            __unpatch_func__ unpatch function from the integration.

        Example:
        A simple implementation inheriting this TestCase looks like::

            from ddtrace.contrib.internal.redis.patch import patch, unpatch

            class RedisPatchTestCase(PatchTestCase.Base):
                __integration_name__ = 'redis'
                __module_name__ 'redis'
                __patch_func__ = patch
                __unpatch_func__ = unpatch

                def assert_module_patched(self, redis):
                    # assert patching logic
                    # self.assert_wrapped(...)

                def assert_not_module_patched(self, redis):
                    # assert patching logic
                    # self.assert_not_wrapped(...)

                def assert_not_module_double_patched(self, redis):
                    # assert patching logic
                    # self.assert_not_double_wrapped(...)

                # override this particular test case
                def test_patch_import(self):
                    # custom patch before import check

                # optionally override other test methods...
        """

        __integration_name__ = None
        __module_name__ = None
        __patch_func__ = None
        __unpatch_func__ = None
        __get_version__ = None
        __get_versions__ = None
        __supported_versions__ = None

        def __init__(self, *args, **kwargs):
            # DEV: Python will wrap a function when assigning it to a class as an
            # attribute. So we cannot call self.__unpatch_func__() because the `self`
            # reference will be passed as an argument.
            # So we need to unwrap the function and then wrap it in a function
            # that will absorb the unpatch function.
            if self.__unpatch_func__:
                unpatch_func = self.__unpatch_func__.__func__

                def unpatch():
                    unpatch_func()

                self.__unpatch_func__ = unpatch

            # Same for __patch_func__()
            if self.__patch_func__:
                patch_func = self.__patch_func__.__func__

                def patch():
                    patch_func()

                self.__patch_func__ = patch

            # Same for __get_version__()
            if self.__get_version__:
                get_version_func = self.__get_version__.__func__

                def get_version():
                    return get_version_func()

                self.__get_version__ = get_version

            # Same for __get_version__()
            if self.__get_versions__:
                get_versions_func = self.__get_versions__.__func__

                def get_versions():
                    get_versions_func()

                self.__get_versions__ = get_versions

            # we can dynamically import the supported versions function given the module name and the patch file
            _supported_versions = importlib.import_module(
                f"ddtrace.contrib.internal.{self.__integration_name__}.patch"
            )._supported_versions
            self._supported_versions = _supported_versions

            super(PatchTestCase.Base, self).__init__(*args, **kwargs)

        def _gen_test_attrs(self, ops):
            """
            A helper to return test names for tests given a list of different
            operations.
            :return:
            """
            from itertools import permutations

            return ["test_{}".format("_".join(c)) for c in permutations(ops, len(ops))]

        def test_verify_test_coverage(self):
            """
            This TestCase should cover a variety of combinations of importing,
            patching and unpatching.
            """
            tests = []
            tests += self._gen_test_attrs(["import", "patch"])
            tests += self._gen_test_attrs(["import", "patch", "patch"])
            tests += self._gen_test_attrs(["import", "patch", "unpatch"])
            tests += self._gen_test_attrs(["import", "patch", "unpatch", "unpatch"])

            # TODO: it may be possible to generate test cases dynamically. For
            # now focus on the important ones.
            test_ignore = set(
                [
                    "test_unpatch_import_patch",
                    "test_import_unpatch_patch_unpatch",
                    "test_import_unpatch_unpatch_patch",
                    "test_patch_import_unpatch_unpatch",
                    "test_unpatch_import_patch_unpatch",
                    "test_unpatch_import_unpatch_patch",
                    "test_unpatch_patch_import_unpatch",
                    "test_unpatch_patch_unpatch_import",
                    "test_unpatch_unpatch_import_patch",
                    "test_unpatch_unpatch_patch_import",
                ]
            )

            for test_attr in tests:
                if test_attr in test_ignore:
                    continue
                assert hasattr(self, test_attr), "{} not found in expected test attrs".format(test_attr)

        def assert_module_patched(self, module):
            """
            Asserts that the given module is patched.

            For example, the redis integration patches the following methods:
                - redis.StrictRedis.execute_command
                - redis.StrictRedis.pipeline
                - redis.Redis.pipeline
                - redis.client.BasePipeline.execute
                - redis.client.BasePipeline.immediate_execute_command

            So an appropriate assert_module_patched would look like::

                def assert_module_patched(self, redis):
                    self.assert_wrapped(redis.StrictRedis.execute_command)
                    self.assert_wrapped(redis.StrictRedis.pipeline)
                    self.assert_wrapped(redis.Redis.pipeline)
                    self.assert_wrapped(redis.client.BasePipeline.execute)
                    self.assert_wrapped(redis.client.BasePipeline.immediate_execute_command)

            :param module: module to check
            :return: None
            """
            raise NotImplementedError(self.assert_module_patched.__doc__)

        def assert_not_module_patched(self, module):
            """
            Asserts that the given module is not patched.

            For example, the redis integration patches the following methods:
                - redis.StrictRedis.execute_command
                - redis.StrictRedis.pipeline
                - redis.Redis.pipeline
                - redis.client.BasePipeline.execute
                - redis.client.BasePipeline.immediate_execute_command

            So an appropriate assert_not_module_patched would look like::

                def assert_not_module_patched(self, redis):
                    self.assert_not_wrapped(redis.StrictRedis.execute_command)
                    self.assert_not_wrapped(redis.StrictRedis.pipeline)
                    self.assert_not_wrapped(redis.Redis.pipeline)
                    self.assert_not_wrapped(redis.client.BasePipeline.execute)
                    self.assert_not_wrapped(redis.client.BasePipeline.immediate_execute_command)

            :param module:
            :return: None
            """
            raise NotImplementedError(self.assert_not_module_patched.__doc__)

        def assert_not_module_double_patched(self, module):
            """
            Asserts that the given module is not patched twice.

            For example, the redis integration patches the following methods:
                - redis.StrictRedis.execute_command
                - redis.StrictRedis.pipeline
                - redis.Redis.pipeline
                - redis.client.BasePipeline.execute
                - redis.client.BasePipeline.immediate_execute_command

            So an appropriate assert_not_module_double_patched would look like::

                def assert_not_module_double_patched(self, redis):
                    self.assert_not_double_wrapped(redis.StrictRedis.execute_command)
                    self.assert_not_double_wrapped(redis.StrictRedis.pipeline)
                    self.assert_not_double_wrapped(redis.Redis.pipeline)
                    self.assert_not_double_wrapped(redis.client.BasePipeline.execute)
                    self.assert_not_double_wrapped(redis.client.BasePipeline.immediate_execute_command)

            :param module: module to check
            :return: None
            """
            raise NotImplementedError(self.assert_not_module_double_patched.__doc__)

        @raise_if_no_attrs
        def test_import_patch(self):
            """
            The integration should test that each class, method or function that
            is to be patched is in fact done so when ddtrace.patch() is called
            before the module is imported.

            For example:

            an appropriate ``test_patch_import`` would be::

                import redis
                ddtrace.patch(redis=True)
                self.assert_module_patched(redis)
            """
            module = importlib.import_module(self.__module_name__)
            self.assert_not_module_patched(module)
            self.__patch_func__()
            self.assert_module_patched(module)

        @raise_if_no_attrs
        def test_patch_import(self):
            """
            The integration should test that each class, method or function that
            is to be patched is in fact done so when ddtrace.patch() is called
            after the module is imported.

            an appropriate ``test_patch_import`` would be::

                import redis
                ddtrace.patch(redis=True)
                self.assert_module_patched(redis)
            """
            module = importlib.import_module(self.__module_name__)
            self.__patch_func__()
            self.assert_module_patched(module)

        @raise_if_no_attrs
        def test_import_patch_patch(self):
            """
            Proper testing should be done to ensure that multiple calls to the
            integration.patch() method are idempotent. That is, that the
            integration does not patch its library more than once.

            An example for what this might look like for the redis integration::

                import redis
                ddtrace.patch(redis=True)
                self.assert_module_patched(redis)
                ddtrace.patch(redis=True)
                self.assert_not_module_double_patched(redis)
            """
            self.__patch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_module_patched(module)
            self.__patch_func__()
            self.assert_not_module_double_patched(module)

        @raise_if_no_attrs
        def test_patch_import_patch(self):
            """
            Proper testing should be done to ensure that multiple calls to the
            integration.patch() method are idempotent. That is, that the
            integration does not patch its library more than once.

            An example for what this might look like for the redis integration::

                ddtrace.patch(redis=True)
                import redis
                self.assert_module_patched(redis)
                ddtrace.patch(redis=True)
                self.assert_not_module_double_patched(redis)
            """
            self.__patch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_module_patched(module)
            self.__patch_func__()
            self.assert_not_module_double_patched(module)

        @raise_if_no_attrs
        def test_patch_patch_import(self):
            """
            Proper testing should be done to ensure that multiple calls to the
            integration.patch() method are idempotent. That is, that the
            integration does not patch its library more than once.

            An example for what this might look like for the redis integration::

                ddtrace.patch(redis=True)
                ddtrace.patch(redis=True)
                import redis
                self.assert_not_double_wrapped(redis.StrictRedis.execute_command)
            """
            self.__patch_func__()
            self.__patch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_module_patched(module)
            self.assert_not_module_double_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_import_patch_unpatch_patch(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it and then subsequently
            patch it again.

            For example::

                import redis
                from ddtrace.contrib.internal.redis.patch import unpatch

                ddtrace.patch(redis=True)
                unpatch()
                ddtrace.patch(redis=True)
                self.assert_module_patched(redis)
            """
            module = importlib.import_module(self.__module_name__)
            self.__patch_func__()
            self.assert_module_patched(module)
            self.__unpatch_func__()
            self.assert_not_module_patched(module)
            self.__patch_func__()
            self.__patch_func__()
            self.assert_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_patch_import_unpatch_patch(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it and then subsequently
            patch it again.

            For example::

                from ddtrace.contrib.internal.redis.patch import unpatch

                ddtrace.patch(redis=True)
                import redis
                unpatch()
                ddtrace.patch(redis=True)
                self.assert_module_patched(redis)
            """
            self.__patch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_module_patched(module)
            self.__unpatch_func__()
            self.assert_not_module_patched(module)
            self.__patch_func__()
            self.assert_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_patch_unpatch_import_patch(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it and then subsequently
            patch it again.

            For example::

                from ddtrace.contrib.internal.redis.patch import unpatch

                ddtrace.patch(redis=True)
                import redis
                unpatch()
                ddtrace.patch(redis=True)
                self.assert_module_patched(redis)
            """
            self.__patch_func__()
            self.__unpatch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_not_module_patched(module)
            self.__patch_func__()
            self.assert_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_patch_unpatch_patch_import(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it and then subsequently
            patch it again.

            For example::

                from ddtrace.contrib.internal.redis.patch import unpatch

                ddtrace.patch(redis=True)
                unpatch()
                ddtrace.patch(redis=True)
                import redis
                self.assert_module_patched(redis)
            """
            self.__patch_func__()
            self.__unpatch_func__()
            self.__patch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_unpatch_patch_import(self):
            """
            Make sure unpatching before patch does not break patching.

            For example::

                from ddtrace.contrib.internal.redis.patch import unpatch
                unpatch()
                ddtrace.patch(redis=True)
                import redis
                self.assert_not_module_patched(redis)
            """
            self.__unpatch_func__()
            self.__patch_func__()
            self.__patch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_patch_unpatch_import(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it before importing the
            library.

            For example::

                ddtrace.patch(redis=True)
                from ddtrace.contrib.internal.redis.patch import unpatch
                unpatch()
                import redis
                self.assert_not_module_patched(redis)
            """
            self.__patch_func__()
            self.__unpatch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_not_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_import_unpatch_patch(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it before patching.

            For example::

                import redis
                from ddtrace.contrib.internal.redis.patch import unpatch
                ddtrace.patch(redis=True)
                unpatch()
                self.assert_not_module_patched(redis)
            """
            module = importlib.import_module(self.__module_name__)
            self.__unpatch_func__()
            self.assert_not_module_patched(module)
            self.__patch_func__()
            self.assert_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_import_patch_unpatch(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it after patching.

            For example::

                import redis
                from ddtrace.contrib.internal.redis.patch import unpatch
                ddtrace.patch(redis=True)
                unpatch()
                self.assert_not_module_patched(redis)
            """
            module = importlib.import_module(self.__module_name__)
            self.assert_not_module_patched(module)
            self.__patch_func__()
            self.assert_module_patched(module)
            self.__unpatch_func__()
            self.assert_not_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_patch_import_unpatch(self):
            """
            To ensure that we can thoroughly test the installation/patching of
            an integration we must be able to unpatch it after patching.

            For example::

                from ddtrace.contrib.internal.redis.patch import unpatch
                ddtrace.patch(redis=True)
                import redis
                unpatch()
                self.assert_not_module_patched(redis)
            """
            self.__patch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_module_patched(module)
            self.__unpatch_func__()
            self.assert_not_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_import_patch_unpatch_unpatch(self):
            """
            Unpatching twice should be a no-op.

            For example::

                import redis
                from ddtrace.contrib.internal.redis.patch import unpatch

                ddtrace.patch(redis=True)
                self.assert_module_patched(redis)
                unpatch()
                self.assert_not_module_patched(redis)
                unpatch()
                self.assert_not_module_patched(redis)
            """
            module = importlib.import_module(self.__module_name__)
            self.__patch_func__()
            self.assert_module_patched(module)
            self.__unpatch_func__()
            self.assert_not_module_patched(module)
            self.__unpatch_func__()
            self.assert_not_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_patch_unpatch_import_unpatch(self):
            """
            Unpatching twice should be a no-op.

            For example::

                from ddtrace.contrib.internal.redis.patch import unpatch

                ddtrace.patch(redis=True)
                unpatch()
                import redis
                self.assert_not_module_patched(redis)
                unpatch()
                self.assert_not_module_patched(redis)
            """
            self.__patch_func__()
            self.__unpatch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_not_module_patched(module)
            self.__unpatch_func__()
            self.assert_not_module_patched(module)

        @noop_if_no_unpatch
        @raise_if_no_attrs
        def test_patch_unpatch_unpatch_import(self):
            """
            Unpatching twice should be a no-op.

            For example::

                from ddtrace.contrib.internal.redis.patch import unpatch

                ddtrace.patch(redis=True)
                unpatch()
                unpatch()
                import redis
                self.assert_not_module_patched(redis)
            """
            self.__patch_func__()
            self.__unpatch_func__()
            self.__unpatch_func__()
            module = importlib.import_module(self.__module_name__)
            self.assert_not_module_patched(module)

        def test_ddtrace_run_patch_on_import(self):
            # We check that the integration's patch function is called only
            # after import of the relevant module when using ddtrace-run.
            with NamedTemporaryFile(mode="w", suffix=".py") as f:
                f.write(
                    dedent(
                        """
                        import sys

                        from ddtrace.internal.module import ModuleWatchdog

                        from wrapt import wrap_function_wrapper as wrap

                        patched = False

                        def patch_hook(module):
                            def patch_wrapper(wrapped, _, args, kwrags):
                                global patched

                                result = wrapped(*args, **kwrags)
                                sys.stdout.write("K")
                                patched = True
                                return result

                            wrap(module.__name__, module.patch.__name__, patch_wrapper)

                        ModuleWatchdog.register_module_hook("ddtrace.contrib.internal.%s.patch", patch_hook)

                        sys.stdout.write("O")

                        import %s as mod

                        # If the module was already loaded during the sitecustomize
                        # we check that the module was marked as patched.
                        if not patched and (
                            getattr(mod, "__datadog_patch", False) or getattr(mod, "_datadog_patch", False)
                        ):
                            sys.stdout.write("K")
                        """
                        % (self.__integration_name__, self.__module_name__)
                    )
                )
                f.flush()

                env = os.environ.copy()
                env["DD_TRACE_%s_ENABLED" % self.__integration_name__.upper()] = "1"

                out, err, _, _ = call_program("ddtrace-run", sys.executable, f.name, env=env)

                self.assertEqual(out, b"OK", "stderr:\n%s" % err.decode())

        def test_and_emit_get_version(self):
            """Each contrib module should implement a get_version() function. This function is used for
            the APM Telemetry integrations payload event, and by APM analytics to inform of dd-trace-py
            current supported integration versions.
            """
            if hasattr(self, "__get_versions__") and self.__get_versions__ is not None:
                assert self.__get_version__() == ""
                versions = self.__get_versions__()
                assert self.__module_name__ in versions
                assert versions[self.__module_name__] != ""
                for name, v in versions.items():
                    emit_integration_and_version_to_test_agent(self.__integration_name__, v, module_name=name)
            else:
                version = self.__get_version__()
                assert type(version) == str
                assert version != ""
                emit_integration_and_version_to_test_agent(
                    self.__integration_name__, version, module_name=self.__module_name__
                )

        def test_supported_versions_function_exists(self):
            """
            Test the integration's supported versions are correctly reported via the '_supported_versions()' method.
            """
            assert hasattr(self, "_supported_versions") is not False
            versions = self._supported_versions()

            module_name = self.__module_name__
            if module_name not in versions:
                # some integration modules are not named the same as the module they are patching
                from ddtrace._monkey import _MODULES_FOR_CONTRIB

                if module_name in _MODULES_FOR_CONTRIB:
                    module_name = _MODULES_FOR_CONTRIB[module_name][0]
                else:
                    # this may be a submodule we are importing, so get the top level module name
                    # ie: snowflake.connector -> snowflake
                    module_name = module_name.split(".")[0]

                assert module_name in versions
            else:
                assert module_name in versions

            assert versions[module_name] != ""

        def test_supported_versions_function_allows_valid_imports(self):
            """
            Test the integration's supported versions allows valid imports.
            """
            with NamedTemporaryFile(mode="w", suffix=".py") as f:
                f.write(
                    dedent(
                        """
                        import sys
                        from ddtrace.internal.module import ModuleWatchdog
                        from wrapt import wrap_function_wrapper as wrap

                        supported_versions_called = False
                        patch_module = None

                        def patch_hook(module):
                            def supported_versions_wrapper(wrapped, _, args, kwrags):
                                global supported_versions_called
                                result = wrapped(*args, **kwrags)
                                sys.stdout.write("K")
                                supported_versions_called = True
                                return result

                            def patch_wrapper(wrapped, _, args, kwrags):
                                result = wrapped(*args, **kwrags)
                                sys.stdout.write("K")
                                return result

                            patch_module = module
                            if 'patch' not in module.__name__:
                                patch_module = module.patch

                            wrap(module.__name__, module.patch.__name__, patch_wrapper)
                            wrap(
                                patch_module.__name__,
                                patch_module._supported_versions.__name__,
                                supported_versions_wrapper,
                            )

                        ModuleWatchdog.register_module_hook("ddtrace.contrib.internal.%s.patch", patch_hook)

                        sys.stdout.write("O")

                        import %s as mod

                        installed_version = patch_module.get_version()
                        if not installed_version:
                            # if installed version is None, the module is a stdlib module
                            # and ``_supported_versions`` will not have been called
                            sys.stdout.write("K")

                        # If the module was already loaded during the sitecustomize
                        # we check that the module was marked as patched.
                        if not supported_versions_called and (
                            getattr(mod, "__datadog_patch", False) or getattr(mod, "_datadog_patch", False)
                        ):
                            sys.stdout.write("K")
                        """
                        % (self.__integration_name__, self.__module_name__)
                    )
                )
                f.flush()

                env = os.environ.copy()
                env["DD_TRACE_SAFE_INSTRUMENTATION_ENABLED"] = "1"
                env["DD_TRACE_%s_ENABLED" % self.__integration_name__.upper()] = "1"

                out, err, _, _ = call_program("ddtrace-run", sys.executable, f.name, env=env)
                assert "OKK" in out.decode(), "stderr:\n%s" % err.decode()
