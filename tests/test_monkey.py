import mock
import pytest
import unittest

from ddtrace.monkey import (
    PatchException,
    install,
    install_all,
    integration_modname,
    patch,
    patch_all,
)


@mock.patch('ddtrace.monkey._BASE_MODULENAME', 'ddtrace.contrib')
class TestInstall(unittest.TestCase):
    def setUp(self):
        # DEV: we have to mock importlib because ``mock`` cannot patch
        # the integration modules due to us importing them dynamically
        # in ``install``.
        self.importlib_patcher = mock.patch('importlib.import_module')
        mock_import_module = self.importlib_patcher.start()

        self.mock_integration1 = mock.MagicMock()
        self.mock_integration2 = mock.MagicMock()
        self.mock_integration3 = mock.MagicMock()

        def my_import_module(mod):
            if mod == 'ddtrace.contrib.integration1':
                return self.mock_integration1
            if mod == 'ddtrace.contrib.integration2':
                return self.mock_integration2
            if mod == 'ddtrace.contrib.integration3':
                return self.mock_integration3

            # Emulate module not existing
            raise ImportError()

        mock_import_module.side_effect = my_import_module

    def tearDown(self):
        self.importlib_patcher.stop()

    def test_integration_modname(self):
        intmodname = integration_modname('integration1', 'ddtrace.contrib')
        assert intmodname == 'ddtrace.contrib.integration1'

    def test_install(self):
        """
        Test installing an integration.
        """
        install('integration1')
        self.mock_integration1.patch.assert_called_once()

    def test_install_integration_does_not_exist(self):
        """
        Test installing an integration that does not exist.

        When raise_errors is not defined
            ``install`` should not raise errors
        When raise_errors is True
            ``install`` should raise an PatchException
        """
        with mock.patch('ddtrace.monkey.log') as log_mock:
            install('nonexistent_integration')
            log_mock.error.assert_called_once_with(
                'install: integration nonexistent_integration not found'
            )

        with pytest.raises(PatchException) as error:
            install('nonexistent_integration', raise_errors=True)
        assert 'install: integration nonexistent_integration not found' in str(error)

    @mock.patch('ddtrace.monkey.DEFAULT_INTEGRATIONS', {
        'integration1': True,
        'integration2': True,
        'integration3': True,
    })
    def test_install_all_defaults(self):
        install_all()
        self.mock_integration1.patch.assert_called_once()
        self.mock_integration2.patch.assert_called_once()
        self.mock_integration3.patch.assert_called_once()

    @mock.patch('ddtrace.monkey.DEFAULT_INTEGRATIONS', {
        'integration1': True,
        'integration2': True,
    })
    def test_install_all_overrides(self):
        """
        """
        install_all(overrides={
            'integration1': False
        })
        assert not self.mock_integration1.patch.called
        self.mock_integration2.patch.assert_called_once()

    @mock.patch('ddtrace.monkey.DEFAULT_INTEGRATIONS', {
        'integration1': True,
        'nonexistent_integration1': True,
        'nonexistent_integration2': True,
    })
    def test_install_all_integration_does_not_exist(self):
        """
        Test installing an integration that does not exist.

        When raise_errors is not defined
            ``install_all`` should not raise errors
        When raise_errors is True
            ``install_all`` should raise an PatchException
        """
        with mock.patch('ddtrace.monkey.log') as log_mock:
            install_all()
            calls = [
                mock.call('install: integration nonexistent_integration1 not found'),
                mock.call('install: integration nonexistent_integration2 not found')
            ]
            log_mock.error.assert_has_calls(calls, any_order=True)

        # integration1.patch() still should have been called
        self.mock_integration1.patch.assert_called_once()

        with pytest.raises(PatchException):
            install_all(raise_errors=True)

    @mock.patch('ddtrace.monkey.DEFAULT_INTEGRATIONS', {
        'integration1': True,
        'integration2': True,
        'integration3': True,
    })
    def test_patch(self):
        """
        Test that ``patch`` will invoke the corresponding ``patch`` function
        for the given integrations but not on the default enabled integrations.
        """
        patch(integration1=True, integration2=False)

        self.mock_integration1.patch.assert_called_once()
        assert not self.mock_integration2.patch.called
        assert not self.mock_integration3.patch.called

    def test_patch_integration_does_not_exist(self):
        """
        Test patching an integration that does not exist.

        When raise_errors is False
            ``patch`` should not raise an PatchException
        When raise_errors is not defined
            ``patch`` should raise errors
        """
        with mock.patch('ddtrace.monkey.log') as log_mock:
            patch(raise_errors=False, dne1=True, dne2=True)
            calls = [
                mock.call('install: integration dne1 not found'),
                mock.call('install: integration dne2 not found')
            ]
            log_mock.error.assert_has_calls(calls, any_order=True)

        with pytest.raises(PatchException):
            patch(dne1=True, dne2=True)

    @mock.patch('ddtrace.monkey.DEFAULT_INTEGRATIONS', {
        'integration1': True,
        'integration2': True,
        'integration3': False,
    })
    def test_patch_all_defaults(self):
        """
        Test that ``patch_all`` will invoke ``patch`` function for all the
        DEFAULT_INSTALLED integrations.
        """
        patch_all()

        self.mock_integration1.patch.assert_called_once()
        self.mock_integration2.patch.assert_called_once()
        assert not self.mock_integration3.patch.called

    @mock.patch('ddtrace.monkey.DEFAULT_INTEGRATIONS', {
        'integration1': True,
        'integration2': True,
        'integration3': False,
    })
    def test_patch_all_overrides(self):
        """
        Test that ``patch_all`` will not invoke ``patch`` function for
        overridden integrations but still install the default enabled
        integrations.
        """
        patch_all(integration2=False)

        self.mock_integration1.patch.assert_called_once()
        assert not self.mock_integration2.patch.called
        assert not self.mock_integration3.patch.called

    @mock.patch('ddtrace.monkey.DEFAULT_INTEGRATIONS', {
        'integration1': True,
    })
    def test_patch_all_integration_does_not_exist(self):
        """
        Test patching an integration that does not exist.

        When an integration is not found
            ``patch_all`` should not raise errors
        """
        with mock.patch('ddtrace.monkey.log') as log_mock:
            patch_all(dne1=True, dne2=True)
            calls = [
                mock.call('install: integration dne1 not found'),
                mock.call('install: integration dne2 not found')
            ]
            log_mock.error.assert_has_calls(calls, any_order=True)

        # integration1.patch should still be called
        self.mock_integration1.patch.assert_called_once()
