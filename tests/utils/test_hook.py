import mock

from ddtrace.compat import PY2, reload_module
from ddtrace.utils.hook import (
    register_post_import_hook,
    deregister_post_import_hook,
)

from tests.subprocesstest import SubprocessTestCase, run_in_subprocess


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
        appropriate log debug message.
        """
        test_hook = mock.MagicMock()
        with mock.patch('ddtrace.utils.hook.log') as log_mock:
            import tests.utils.test_module  # noqa
            register_post_import_hook('tests.utils.test_module', test_hook)
            test_hook.assert_called_once()
            calls = [
                mock.call('module "tests.utils.test_module" already imported, firing hook')
            ]
            log_mock.debug.assert_has_calls(calls)

    def test_register_post_import_hook_reimport(self):
        """
        Test that a hook is fired when the module is reimported.
        """
        test_hook = mock.MagicMock()
        register_post_import_hook('tests.utils.test_module', test_hook)
        import tests.utils.test_module
        reload_module(tests.utils.test_module)
        self.assertEqual(test_hook.call_count, 2)

    def test_register_post_import_hook_multiple(self):
        """
        Test that multiple hooks are fired after registering.
        """
        test_hook = mock.MagicMock()
        test_hook2 = mock.MagicMock()
        register_post_import_hook('tests.utils.test_module', test_hook)
        register_post_import_hook('tests.utils.test_module', test_hook2)
        import tests.utils.test_module  # noqa
        test_hook.assert_called_once()
        test_hook2.assert_called_once()

    def test_register_post_import_hook_different_modules(self):
        """
        Test that multiple hooks hooked on different modules are fired after registering.
        """
        test_hook = mock.MagicMock()
        test_hook_redis = mock.MagicMock()
        register_post_import_hook('tests.utils.test_module', test_hook)
        register_post_import_hook('ddtrace.contrib.redis', test_hook_redis)
        import tests.utils.test_module  # noqa
        import ddtrace.contrib.redis  # noqa
        test_hook.assert_called_once()
        test_hook_redis.assert_called_once()

    def test_register_post_import_hook_duplicate_register(self):
        """
        Test that a function can be registered as a hook twice.
        """
        test_hook = mock.MagicMock()
        register_post_import_hook('tests.utils.test_module', test_hook)
        register_post_import_hook('tests.utils.test_module', test_hook)
        import tests.utils.test_module  # noqa
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

    def test_deregister_post_import_hook_after_register_multiple(self):
        """
        Test that multiple import hooks can be deregistered.
        """
        test_hook = mock.MagicMock()
        test_hook2 = mock.MagicMock()
        setattr(test_hook, '_test', True)
        setattr(test_hook2, '_test', True)
        register_post_import_hook('tests.utils.test_module', test_hook)
        register_post_import_hook('tests.utils.test_module', test_hook2)

        def matcher(hook):
            return hasattr(hook, '_test')

        deregister_post_import_hook('tests.utils.test_module', matcher)
        import tests.utils.test_module  # noqa
        self.assertEqual(test_hook.call_count, 0, 'hook has been deregistered and should be removed')
        self.assertEqual(test_hook2.call_count, 0, 'hook has been deregistered and should be removed')

    def test_deregister_post_import_hook_after_register_multiple_one_match(self):
        """
        Test that only specified import hooks can be deregistered after being registered.
        """
        test_hook = mock.MagicMock()
        test_hook2 = mock.MagicMock()
        setattr(test_hook, '_test', True)
        setattr(test_hook2, '_test2', True)
        register_post_import_hook('tests.utils.test_module', test_hook)
        register_post_import_hook('tests.utils.test_module', test_hook2)

        def matcher(hook):
            return hasattr(hook, '_test')

        deregister_post_import_hook('tests.utils.test_module', matcher)
        import tests.utils.test_module  # noqa
        self.assertEqual(test_hook.call_count, 0, 'hook has been deregistered and should be removed')
        self.assertEqual(test_hook2.call_count, 0, 'hook has been deregistered and should be removed')

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
        reload_module(tests.utils.test_module)
        self.assertEqual(test_hook.call_count, 1, 'hook should only be called once')

    def test_hook_exception(self):
        """
        Test that when a hook throws an exception that it is caught and logged
        as a warning.
        """
        def test_hook(module):
            raise Exception('test_hook_failed')
        register_post_import_hook('tests.utils.test_module', test_hook)

        with mock.patch('ddtrace.utils.hook.log') as log_mock:
            import tests.utils.test_module
            calls = [
                mock.call('hook for module "tests.utils.test_module" failed: test_hook_failed')
            ]
            log_mock.warn.assert_has_calls(calls)

    def test_hook_called_with_module(self):
        """
        Test that a hook is called with the module that it is hooked on.
        """
        def test_hook(module):
            self.assertTrue(hasattr(module, 'A'))
        register_post_import_hook('tests.utils.test_module', test_hook)
        import tests.utils.test_module  # noqa
