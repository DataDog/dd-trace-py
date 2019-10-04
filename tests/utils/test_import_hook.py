import mock
import sys
import types

from tests.subprocesstest import SubprocessTestCase, run_in_subprocess

from ddtrace.utils.import_hook import (
    install_module_import_hook,
    uninstall_module_import_hook,
    _mark_module_patched,
    _mark_module_unpatched,
    module_patched,
)


@run_in_subprocess
class TestInstallUtils(SubprocessTestCase):
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
        install_module_import_hook('tests.utils.test_module', test_hook)
        assert not test_hook.called, 'test_hook should not be called until module import'

        import tests.utils.test_module  # noqa
        test_hook.assert_called_once()

    def test_install_module_import_hook_reload(self):
        """
        Import hooks should only be called once if the hooked module is imported
        twice.
        """
        def hook(mod):
            def patch_fn(self):
                return 2
            mod.A.fn = patch_fn
        install_module_import_hook('tests.utils.test_module', hook)

        import tests.utils.test_module  # noqa
        self.assertEqual(tests.utils.test_module.A().fn(), 2)

        del sys.modules['tests.utils.test_module']
        import tests.utils.test_module  # noqa
        self.assertEqual(tests.utils.test_module.A().fn(), 2)

    def test_install_module_import_hook_already_imported(self):
        """
        Test that import hooks are fired when the module is already imported.
        """
        import tests.utils.test_module  # noqa
        test_hook = mock.MagicMock()
        install_module_import_hook('tests.utils.test_module', test_hook)
        test_hook.assert_called_once()

    def test_uninstall_module_import_hook(self):
        """
        Test that a module import hook can be uninstalled.
        """
        test_hook = mock.MagicMock()
        install_module_import_hook('tests.utils.test_module', test_hook)
        uninstall_module_import_hook('tests.utils.test_module')
        import tests.utils.test_module  # noqa
        self.assertEqual(test_hook.called, 0, 'test_hook should not be called')

    def test_install_uninstall_install(self):
        """
        Test that a module import hook can be installed, uninstalled and
        reinstalled.
        """
        test_hook = mock.MagicMock()
        install_module_import_hook('tests.utils.test_module', test_hook)
        uninstall_module_import_hook('tests.utils.test_module')
        install_module_import_hook('tests.utils.test_module', test_hook)
        import tests.utils.test_module  # noqa
        test_hook.assert_called_once()

    def test_hook_exception(self):
        """
        When a hook raises an exception
            the exception should be caught and logged
        """
        def err_hook(module):
            raise Exception('module hook failed')

        with mock.patch('ddtrace.utils.hook.log') as log_mock:
            install_module_import_hook('tests.utils.test_module', err_hook)
            import tests.utils.test_module  # noqa
            calls = [
                mock.call('failed to call hook for module "tests.utils.test_module": module hook failed'),
            ]
            import sys
            sys.stderr.write(str(log_mock.warn.mock_calls))
            log_mock.warn.assert_has_calls(calls, any_order=True)

