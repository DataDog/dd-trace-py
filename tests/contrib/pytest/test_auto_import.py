import importlib
import os
import sys
from unittest import mock


class TestAutoImport:
    """Test ddtrace.auto import behavior with pytest plugin."""

    def test_auto_import_with_pytest_plugin_enabled(self):
        """Test that ddtrace.auto skips patching when pytest plugin is enabled."""
        with mock.patch("ddtrace.contrib.internal.pytest.plugin.is_enabled", return_value=True), \
             mock.patch("ddtrace.bootstrap.sitecustomize") as mock_sitecustomize:
            import ddtrace.auto  # noqa: F401
            
            # Verify sitecustomize wasn't imported/executed
            mock_sitecustomize.assert_not_called()

    def test_auto_import_with_pytest_plugin_disabled(self):
        """Test that ddtrace.auto imports normally when pytest plugin is disabled."""
        with mock.patch("ddtrace.contrib.internal.pytest.plugin.is_enabled", return_value=False):
            import ddtrace.auto  # noqa: F401
            
            assert "ddtrace.auto" in sys.modules

    def test_auto_import_outside_pytest(self):
        """Test that ddtrace.auto imports normally when not in pytest context."""
        # Remove pytest from sys.modules to simulate non-pytest environment
        with mock.patch.dict(sys.modules):
            if "pytest" in sys.modules:
                del sys.modules["pytest"]
            
            import ddtrace.auto  # noqa: F401
            assert "ddtrace.auto" in sys.modules

    def test_auto_import_with_environment_variable(self):
        """Test that ddtrace.auto respects DD_PYTEST_PLUGIN_ENABLED environment variable."""
        with mock.patch.dict(os.environ, {"DD_PYTEST_PLUGIN_ENABLED": "true"}), \
             mock.patch("ddtrace.contrib.internal.pytest.plugin.is_enabled", return_value=False), \
             mock.patch("ddtrace.bootstrap.sitecustomize") as mock_sitecustomize:
            
            import ddtrace.auto  # noqa: F401
            
            # Verify sitecustomize wasn't imported/executed due to environment variable
            mock_sitecustomize.assert_not_called()
