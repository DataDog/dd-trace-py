import mock

from ddtrace.compat import PY2
from ddtrace.utils.hook import (
    register_post_import_hook,
    deregister_post_import_hook,
)

from tests.subprocesstest import SubprocessTestCase, run_in_subprocess


if PY2:
    reload = reload
else:
    import importlib
    reload = importlib.reload


@run_in_subprocess
class TestHook(SubprocessTestCase):
    def test_register_post_import_hook_before_import(self):
        """
        Test that a hook is fired after registering.
        """
        test_hook = mock.MagicMock()
        register_post_import_hook('tests.utils.test_module', test_hook)
        import tests.utils.test_module  # noqa
        test_hook.assert_called_once()

    def test_register_post_import_hook_after_import(self):
        """
        Test that a hook is fired when the module is imported with an
        appropriate log warning message.
        """
        test_hook = mock.MagicMock()
        with mock.patch('ddtrace.utils.hook.log') as log_mock:
            import tests.utils.test_module  # noqa
            register_post_import_hook('tests.utils.test_module', test_hook)
            test_hook.assert_called_once()
            calls = [
                mock.call('module "tests.utils.test_module" already imported, firing hook')
            ]
            log_mock.warn.assert_has_calls(calls, any_order=True)

    def test_register_post_import_hook_reimport(self):
        """
        Test that a hook is fired when the module is reimported.
        """
        test_hook = mock.MagicMock()
        register_post_import_hook('tests.utils.test_module', test_hook)
        import tests.utils.test_module
        reload(tests.utils.test_module)
        self.assertEqual(test_hook.call_count, 2)

    def test_deregister_post_import_hook_no_register(self):
        """
        Test that deregistering import hooks that do not exist is a no-op.
        """
        def matcher(hook):
            return hasattr(hook, '_test')

        deregister_post_import_hook('tests.utils.test_module', matcher)
        import tests.utils.test_module  # noqa

    def test_deregister_post_import_hook_after_register(self):
        """
        Test that import hooks can be deregistered after being registered.
        """
        test_hook = mock.MagicMock()
        setattr(test_hook, '_test', True)
        register_post_import_hook('tests.utils.test_module', test_hook)

        def matcher(hook):
            return hasattr(hook, '_test')

        deregister_post_import_hook('tests.utils.test_module', matcher)
        import tests.utils.test_module  # noqa
        self.assertEqual(test_hook.call_count, 0, 'hook has been deregistered and should be removed')

    def test_deregister_post_import_hook_after_import(self):
        """
        Test that import hooks can be deregistered after being registered.
        """
        test_hook = mock.MagicMock()
        setattr(test_hook, '_test', True)
        register_post_import_hook('tests.utils.test_module', test_hook)

        def matcher(hook):
            return hasattr(hook, '_test')

        import tests.utils.test_module
        test_hook.assert_called_once()
        deregister_post_import_hook('tests.utils.test_module', matcher)
        reload(tests.utils.test_module)
        self.assertEqual(test_hook.call_count, 1, 'hook should only be called once')
