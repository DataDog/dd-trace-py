import contextlib
import sys

import mock
import pytest

from ddtrace.internal import import_hooks
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess


import_hooks.patch()


@pytest.fixture
def hooks():
    import_hooks.hooks.reset()
    try:
        yield import_hooks.hooks
    finally:
        import_hooks.hooks.reset()


@contextlib.contextmanager
def remove_module(name):
    was_loaded = name in sys.modules
    try:
        # Ensure urllib is not loaded
        if was_loaded:
            del sys.modules[name]
        yield
    finally:
        if not was_loaded:
            del sys.modules[name]


def test_import_hooks(hooks):
    """
    When registering an import hook
        Gets called after the module was imported
    """
    # Ensure our module is not yet loaded
    mod_name = "urllib"
    with remove_module(mod_name):
        # Register our hook (when the module is not loaded)
        module_hook = mock.Mock()
        import_hooks.register_module_hook(mod_name, module_hook)

        # Import the module being hooked
        import urllib

        # Ensure we called our hook with the module
        # DEV: Slightly redundant to check twice, but good to be sure
        module_hook.assert_called_once_with(urllib)
        module_hook.assert_called_once_with(sys.modules[mod_name])


@run_in_subprocess
class ImportHookTestCase(SubprocessTestCase):
    def test_register_then_import(self):
        """
        When an import hook is registered before importing a module
            The import hook should run when the module is imported
        """
        module_hook = mock.Mock()
        import_hooks.register_module_hook("tests.test_module", module_hook)

        # Hook should not be called after register
        module_hook.assert_not_called()

        import tests.test_module

        # Module imported so the hook should be called
        module_hook.assert_called_once_with(tests.test_module)

    def test_import_then_register(self):
        """
        When a module is imported before the import hook is registered
            The import hook should run immediately when registered
        """
        module_hook = mock.Mock()
        import tests.test_module

        module_hook.assert_not_called()
        import_hooks.register_module_hook("tests.test_module", module_hook)

        # Hook should be called on register
        module_hook.assert_called_once_with(tests.test_module)

    def test_no_double_call(self):
        """
        When a module is imported multiple times
            The import hook should only be run once
        """
        module_hook = mock.Mock()
        import_hooks.register_module_hook("tests.test_module", module_hook)
        import tests.test_module  # noqa

        # Hook should be called only once
        module_hook.assert_called_once_with(tests.test_module)

    def test_register_deregister(self):
        """
        When an import hook is registered and subsequently deregistered
            The import hook should not be called
        """
        module_hook = mock.Mock()
        import_hooks.register_module_hook("tests.test_module", module_hook)
        import_hooks.hooks.deregister("tests.test_module", module_hook)
        import tests.test_module  # noqa

        # Hook should not be called
        module_hook.assert_not_called()

    def test_register_multiple_modules(self):
        """
        When registering module hooks on multiple modules
            Each hook is called when their respective module is loaded
        """
        test_module_hook = mock.Mock()
        test_module_hook2 = mock.Mock()
        test_module2_hook = mock.Mock()

        import_hooks.register_module_hook("tests.test_module", test_module_hook)
        import_hooks.register_module_hook("tests.test_module", test_module_hook2)
        import_hooks.register_module_hook("tests.test_module2", test_module2_hook)

        test_module_hook.assert_not_called()
        test_module_hook2.assert_not_called()
        test_module2_hook.assert_not_called()

        import tests.test_module2

        test_module_hook.assert_not_called()
        test_module_hook2.assert_not_called()
        test_module2_hook.assert_called_once_with(tests.test_module2)

        import tests.test_module

        test_module_hook.assert_called_once_with(tests.test_module)
        test_module_hook2.assert_called_once_with(tests.test_module)
        test_module2_hook.assert_called_once_with(tests.test_module2)
