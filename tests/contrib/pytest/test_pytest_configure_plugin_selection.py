"""Unit tests for plugin class selection logic in pytest_configure.

Tests that:
- TestOptPluginWithProtocol is registered when no external rerun plugin is present.
- TestOptPluginWithProtocol is registered when an external rerun plugin is present AND DD retries are enabled
  (DD retries take precedence).
- TestOptPlugin (base, without pytest_runtest_protocol) is registered when an external rerun plugin is present
  AND DD retries are all disabled (external plugin drives retries).
"""

import logging
from unittest import mock

import pytest

from ddtrace.testing.internal.pytest.plugin import _EXTERNAL_RERUN_PLUGINS
from ddtrace.testing.internal.pytest.plugin import TestOptPlugin
from ddtrace.testing.internal.pytest.plugin import TestOptPluginWithProtocol
from ddtrace.testing.internal.pytest.plugin import pytest_configure


_KILL_SWITCH_PATH = "ddtrace.testing.internal.pytest.plugin._is_test_optimization_disabled_by_kill_switch"
_PLUGIN_MODULE = "ddtrace.testing.internal.pytest.plugin"


@pytest.fixture(autouse=True)
def disable_kill_switch():
    with mock.patch(_KILL_SWITCH_PATH, return_value=False):
        yield


def _make_mock_config(plugin_names=(), atr_enabled=False, efd_enabled=False, atf_enabled=False):
    """Return a mock pytest.Config whose pluginmanager.hasplugin reflects plugin_names."""
    config = mock.MagicMock()
    config.pluginmanager.hasplugin.side_effect = lambda name: name in plugin_names
    session_manager = mock.MagicMock()
    session_manager.settings.auto_test_retries.enabled = atr_enabled
    session_manager.settings.early_flake_detection.enabled = efd_enabled
    session_manager.settings.test_management.enabled = atf_enabled
    config.stash.get.return_value = session_manager
    return config, session_manager


class TestPluginClassSelection:
    """Tests that pytest_configure picks the right plugin class based on installed rerun plugins and DD settings."""

    def test_no_external_plugin_registers_with_protocol(self):
        config, session_manager = _make_mock_config()

        with (
            mock.patch(f"{_PLUGIN_MODULE}.TestOptPluginWithProtocol") as MockWithProtocol,
            mock.patch(f"{_PLUGIN_MODULE}.TestOptPlugin"),
        ):
            pytest_configure(config)

        MockWithProtocol.assert_called_once_with(session_manager=session_manager)

    @pytest.mark.parametrize("plugin_name", ["rerunfailures", "flaky"])
    def test_external_plugin_without_dd_retries_registers_base(self, plugin_name):
        """When DD retries are all disabled, external plugin drives execution."""
        config, session_manager = _make_mock_config(plugin_names={plugin_name})

        with (
            mock.patch(f"{_PLUGIN_MODULE}.TestOptPluginWithProtocol") as MockWithProtocol,
            mock.patch(f"{_PLUGIN_MODULE}.TestOptPlugin") as MockBase,
        ):
            pytest_configure(config)

        MockBase.assert_called_once_with(session_manager=session_manager)
        MockWithProtocol.assert_not_called()

    @pytest.mark.parametrize("plugin_name", ["rerunfailures", "flaky"])
    @pytest.mark.parametrize("setting", ["atr_enabled", "efd_enabled"])
    def test_external_plugin_with_dd_retries_registers_with_protocol(self, plugin_name, setting):
        """When any DD retry feature is enabled, we take over regardless of external plugins."""
        config, session_manager = _make_mock_config(plugin_names={plugin_name}, **{setting: True})

        with (
            mock.patch(f"{_PLUGIN_MODULE}.TestOptPluginWithProtocol") as MockWithProtocol,
            mock.patch(f"{_PLUGIN_MODULE}.TestOptPlugin") as MockBase,
        ):
            pytest_configure(config)

        MockWithProtocol.assert_called_once_with(session_manager=session_manager)
        MockBase.assert_not_called()

    @pytest.mark.parametrize("plugin_name,disable_flag", list(_EXTERNAL_RERUN_PLUGINS.items()))
    def test_external_plugin_with_dd_retries_emits_warning(self, plugin_name, disable_flag, caplog):
        config, _ = _make_mock_config(plugin_names={plugin_name}, atr_enabled=True)

        with caplog.at_level(logging.WARNING, logger="ddtrace.testing.internal.pytest.plugin"):
            pytest_configure(config)

        assert plugin_name in caplog.text
        assert "take precedence" in caplog.text

    @pytest.mark.parametrize("plugin_name", ["rerunfailures", "flaky"])
    def test_external_plugin_without_dd_retries_no_warning(self, plugin_name, caplog):
        config, _ = _make_mock_config(plugin_names={plugin_name})

        with caplog.at_level(logging.WARNING, logger="ddtrace.testing.internal.pytest.plugin"):
            pytest_configure(config)

        assert caplog.text == ""

    @pytest.mark.parametrize("plugin_name,disable_flag", list(_EXTERNAL_RERUN_PLUGINS.items()))
    def test_external_plugin_with_test_management_warns_about_atf(self, plugin_name, disable_flag, caplog):
        """When only test management is enabled (no ATR/EFD), external plugin drives but ATF won't work."""
        config, session_manager = _make_mock_config(plugin_names={plugin_name}, atf_enabled=True)

        with caplog.at_level(logging.WARNING, logger="ddtrace.testing.internal.pytest.plugin"):
            pytest_configure(config)

        assert "Attempt to Fix" in caplog.text
        assert disable_flag in caplog.text
        assert session_manager.settings.test_management.enabled is True

    def test_multiple_external_plugins_warns_about_each(self, caplog):
        config, _ = _make_mock_config(plugin_names={"rerunfailures", "flaky"}, efd_enabled=True)

        with caplog.at_level(logging.WARNING, logger="ddtrace.testing.internal.pytest.plugin"):
            pytest_configure(config)

        assert "rerunfailures" in caplog.text
        assert "flaky" in caplog.text

    def test_base_plugin_has_no_runtest_protocol_hook(self):
        """TestOptPlugin must not define pytest_runtest_protocol (the external plugin drives execution)."""
        assert not hasattr(TestOptPlugin, "pytest_runtest_protocol")

    def test_with_protocol_plugin_has_runtest_protocol_hook(self):
        assert hasattr(TestOptPluginWithProtocol, "pytest_runtest_protocol")
        assert callable(TestOptPluginWithProtocol.pytest_runtest_protocol)

    @pytest.mark.parametrize("is_quarantined,is_disabled", [(True, False), (False, True)])
    def test_quarantine_and_disabled_not_applied_when_test_management_disabled(self, is_quarantined, is_disabled):
        """When test_management is disabled, skip markers and quarantine properties must not be applied."""
        plugin = mock.MagicMock()
        plugin.manager.settings.test_management.enabled = False

        test = mock.MagicMock()
        test.is_quarantined.return_value = is_quarantined
        test.is_disabled.return_value = is_disabled
        test.is_attempt_to_fix.return_value = False

        item = mock.MagicMock()
        item.user_properties = []

        TestOptPlugin._apply_test_management_markers(plugin, item=item, test=test)

        item.add_marker.assert_not_called()
        assert item.user_properties == []

    @pytest.mark.parametrize("is_quarantined,is_disabled", [(True, False), (False, True)])
    def test_quarantine_and_disabled_applied_when_test_management_enabled(self, is_quarantined, is_disabled):
        """When test_management is enabled, markers and properties are applied normally."""
        plugin = mock.MagicMock()
        plugin.manager.settings.test_management.enabled = True

        test = mock.MagicMock()
        test.is_quarantined.return_value = is_quarantined
        test.is_disabled.return_value = is_disabled
        test.is_attempt_to_fix.return_value = False

        item = mock.MagicMock()
        item.user_properties = []

        TestOptPlugin._apply_test_management_markers(plugin, item=item, test=test)

        item.add_marker.assert_called_once()

    @pytest.mark.parametrize(
        "is_quarantined,is_disabled,is_attempt_to_fix",
        [
            (True, False, False),  # quarantined
        ],
    )
    def test_base_plugin_uses_xfail_for_quarantine(self, is_quarantined, is_disabled, is_attempt_to_fix):
        """Base plugin (no retry control) uses xfail for quarantined non-ATF tests."""
        plugin = mock.MagicMock()
        plugin.manager.settings.test_management.enabled = True

        test = mock.MagicMock()
        test.is_quarantined.return_value = is_quarantined
        test.is_disabled.return_value = is_disabled
        test.is_attempt_to_fix.return_value = is_attempt_to_fix

        item = mock.MagicMock()
        item.user_properties = []

        TestOptPlugin._apply_test_management_markers(plugin, item=item, test=test)

        xfail_calls = [
            call
            for call in item.add_marker.call_args_list
            if len(call[0]) > 0 and hasattr(call[0][0], "mark") and call[0][0].mark.name == "xfail"
        ]
        assert len(xfail_calls) == 1
        assert xfail_calls[0][0][0].mark.kwargs["reason"] == "dd_quarantined"
        assert item.user_properties == []

    @pytest.mark.parametrize(
        "is_quarantined,is_disabled,is_attempt_to_fix",
        [
            (False, True, True),  # disabled + ATF
            (True, False, True),  # quarantined + ATF
        ],
    )
    def test_attempt_to_fix_takes_precedence_over_test_management_markers(
        self, is_quarantined, is_disabled, is_attempt_to_fix
    ):
        """ATF tests are not skipped or xfailed, even when quarantined or disabled."""
        plugin = mock.MagicMock()
        plugin.manager.settings.test_management.enabled = True

        test = mock.MagicMock()
        test.is_quarantined.return_value = is_quarantined
        test.is_disabled.return_value = is_disabled
        test.is_attempt_to_fix.return_value = is_attempt_to_fix

        item = mock.MagicMock()
        item.user_properties = []

        for plugin_class in (TestOptPlugin, TestOptPluginWithProtocol):
            item.add_marker.reset_mock()
            item.user_properties = []

            plugin_class._apply_test_management_markers(plugin, item=item, test=test)

            item.add_marker.assert_not_called()
            assert item.user_properties == []

    def test_child_plugin_uses_xfail_for_quarantine_without_atf(self):
        """Child plugin uses xfail for quarantined non-ATF tests (same as base)."""
        plugin = mock.MagicMock()
        plugin.manager.settings.test_management.enabled = True

        test = mock.MagicMock()
        test.is_quarantined.return_value = True
        test.is_disabled.return_value = False
        test.is_attempt_to_fix.return_value = False

        item = mock.MagicMock()
        item.user_properties = []

        TestOptPluginWithProtocol._apply_test_management_markers(plugin, item=item, test=test)

        xfail_calls = [
            call
            for call in item.add_marker.call_args_list
            if len(call[0]) > 0 and hasattr(call[0][0], "mark") and call[0][0].mark.name == "xfail"
        ]
        assert len(xfail_calls) == 1
        assert xfail_calls[0][0][0].mark.kwargs["reason"] == "dd_quarantined"
        assert item.user_properties == []

    @pytest.mark.parametrize("plugin_class", [TestOptPlugin, TestOptPluginWithProtocol])
    def test_plain_atf_no_markers_applied(self, plugin_class):
        """A test that is attempt-to-fix but neither disabled nor quarantined gets no marker or property.
        ATF retry semantics only apply when the test is also disabled or quarantined; plain ATF runs normally.
        """
        plugin = mock.MagicMock()
        plugin.manager.settings.test_management.enabled = True

        test = mock.MagicMock()
        test.is_quarantined.return_value = False
        test.is_disabled.return_value = False
        test.is_attempt_to_fix.return_value = True

        item = mock.MagicMock()
        item.user_properties = []

        plugin_class._apply_test_management_markers(plugin, item=item, test=test)

        item.add_marker.assert_not_called()
        assert item.user_properties == []
