import mock
import unittest
import sys
import types

from wrapt.importer import _post_import_hooks

from ddtrace.utils.install import (
    install_module_import_hook,
    uninstall_module_import_hook,
    _mark_module_patched,
    _mark_module_unpatched,
    module_patched,
)


class TestInstallUtils(unittest.TestCase):
    def tearDown(self):
        self._remove_all_post_import_hooks()

        if 'tests.utils.my_module' in sys.modules:
            del sys.modules['tests.utils.my_module']

    def _remove_all_post_import_hooks(self):
        """Helper to clear all module import hooks."""
        _post_import_hooks.clear()

    def test_mark_module_patched(self):
        module = types.ModuleType('module')
        _mark_module_patched(module)
        self.assertTrue(module_patched(module))

    def test_mark_module_unpatched(self):
        module = types.ModuleType('module')
        _mark_module_unpatched(module)
        self.assertFalse(module_patched(module))

    def test_mark_module_unpatched_after_patch(self):
        module = types.ModuleType('module')
        _mark_module_patched(module)
        _mark_module_unpatched(module)
        self.assertFalse(module_patched(module))

    def test_install_module_import_hook_on_import(self):
        """
        Test that import hooks are called when the module is imported for the
        first time.
        """
        test_hook = mock.MagicMock()
        install_module_import_hook('tests.utils.my_module', test_hook)
        assert not test_hook.called, 'test_hook should not be called until module import'

        import tests.utils.my_module  # noqa
        test_hook.assert_called_once()

    def test_install_module_import_hook_idempotent(self):
        """
        Import hooks should only be called once if the hooked module is
        imported twice.
        """
        def hook(mod):
            def patch_fn(self):
                return 2
            mod.A.fn = patch_fn
        install_module_import_hook('tests.utils.my_module', hook)

        import tests.utils.my_module  # noqa
        assert tests.utils.my_module.A().fn() == 2

        # TODO: the assert below should be
        # >>> assert tests.utils.my_module.A().fn() == 2
        # but wrapt uninstalls the import hooks after they are fired
        # https://github.com/GrahamDumpleton/wrapt/blob/4bcd190457c89e993ffcfec6dad9e9969c033e9e/src/wrapt/importer.py#L127-L136

        # remove the module from sys.modules to force a reimport
        del sys.modules['tests.utils.my_module']
        import tests.utils.my_module  # noqa
        assert tests.utils.my_module.A().fn() == 1

    def test_install_module_import_hook_already_imported(self):
        """
        Test that import hooks are fired when the module is already imported.
        """
        module = types.ModuleType('somewhere')
        sys.modules['some.module.somewhere'] = module
        test_hook = mock.MagicMock()
        install_module_import_hook('some.module.somewhere', test_hook)
        test_hook.assert_called_once()

    def test_uninstall_module_import_hook(self):
        """
        Test that a module import hook can be uninstalled.
        """
        test_hook = mock.MagicMock()
        install_module_import_hook('tests.utils.my_module', test_hook)
        uninstall_module_import_hook('tests.utils.my_module')
        import tests.utils.my_module  # noqa
        assert not test_hook.called, 'test_hook should not be called'

    def test_install_uninstall_install(self):
        """
        Test that a module import hook can be installed, uninstalled and
        reinstalled.
        """
        test_hook = mock.MagicMock()
        install_module_import_hook('tests.utils.my_module', test_hook)
        uninstall_module_import_hook('tests.utils.my_module')
        install_module_import_hook('tests.utils.my_module', test_hook)
        import tests.utils.my_module  # noqa
        test_hook.assert_called_once()
