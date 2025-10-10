import os
import sys
from unittest import mock

import pytest


class TestAutoImport:
    """Test ddtrace.auto import behavior."""

    def test_auto_import_imports_sitecustomize(self):
        """Test that ddtrace.auto imports sitecustomize."""
        with mock.patch("ddtrace.bootstrap.sitecustomize") as mock_sitecustomize:
            import ddtrace.auto  # noqa: F401

            # Verify sitecustomize was imported
            mock_sitecustomize.assert_called_once()

    def test_auto_import_available(self):
        """Test that ddtrace.auto is available after import."""
        import ddtrace.auto  # noqa: F401

        assert "ddtrace.auto" in sys.modules

    @pytest.mark.parametrize("pytest_plugin_enabled", [True, False])
    def test_auto_avoids_double_patching_with_pytest_plugin(self, pytest_plugin_enabled):
        """Test that ddtrace.auto doesn't import sitecustomize when pytest plugin is enabled."""
        fake_pytest = type(sys)("pytest")
        fake_pytest.config = mock.MagicMock()

        with (
            mock.patch.dict("sys.modules", {"pytest": fake_pytest}),
            mock.patch("ddtrace.bootstrap.sitecustomize") as mock_sitecustomize,
            mock.patch(
                "ddtrace.contrib.internal.pytest.plugin.is_enabled", return_value=pytest_plugin_enabled
            ) as mock_is_enabled,
        ):
            import ddtrace.auto  # noqa: F401

            mock_is_enabled.assert_called_once_with(fake_pytest.config)

            if pytest_plugin_enabled:
                mock_sitecustomize.assert_not_called()
            else:
                mock_sitecustomize.assert_called_once()
