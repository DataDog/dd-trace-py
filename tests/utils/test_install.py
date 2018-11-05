import mock
import unittest
import types

from ddtrace.utils.install import (
    install_module_import_hook,
    mark_module_patched,
    mark_module_unpatched,
    module_patched,
)


class TestInstallUtils(unittest.TestCase):
    def test_mark_module_patched(self):
        module = types.ModuleType('module')
        mark_module_patched(module)
        self.assertTrue(module_patched(module))

    def test_mark_module_unpatched(self):
        module = types.ModuleType('module')
        mark_module_unpatched(module)
        self.assertFalse(module_patched(module))

    def test_mark_module_unpatched_after_patch(self):
        module = types.ModuleType('module')
        mark_module_patched(module)
        mark_module_unpatched(module)
        self.assertFalse(module_patched(module))

    def test_install_module_import_hook_on_import(self):
        # tests that import hooks are called when the module is imported for
        # the first time

        # remove the test module if it exists in sys modules
        import sys
        del sys.modules['tests.utils.my_module']

        test_hook = mock.MagicMock()
        assert 'tests.utils.my_module' not in sys.modules, 'my_module should not be in sys.modules'
        install_module_import_hook('tests.utils.my_module', test_hook)
        assert not test_hook.called, 'test_hook should not be called until module import'

        import tests.utils.my_module
        test_hook.assert_called_once()

    def test_install_module_import_hook_already_import(self):
        # tests that import hooks are fired when the module is already
        # imported

        import sys
        module = types.ModuleType('somewhere')
        sys.modules['some.module.somewhere'] = module
        test_hook = mock.MagicMock()
        install_module_import_hook('some.module.somewhere', test_hook)
        test_hook.assert_called_once()
