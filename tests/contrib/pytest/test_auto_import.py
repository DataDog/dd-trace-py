import importlib
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

    def test_ddtrace_run_imports_sitecustomize(self):
        """Test that ddtrace-run properly imports sitecustomize when running a script."""
        import subprocess
        import sys

        # Get the path to the test script in the fixtures directory
        test_script = os.path.join(os.path.dirname(__file__), "fixtures", "ddtrace_run_test_script.py")

        # Run the test script using ddtrace-run
        result = subprocess.run([sys.executable, "-m", "ddtrace.run", test_script], capture_output=True, text=True)

        # Check the output and return code
        if result.returncode != 0:
            print(f"Test script failed with error:\n{result.stderr}")
        assert result.returncode == 0, f"ddtrace-run failed to import sitecustomize\n{result.stderr}"
        assert "SUCCESS: sitecustomize was imported by ddtrace-run" in result.stdout

    @pytest.mark.parametrize("pytest_plugin_enabled", [True, False])
    def test_auto_avoids_double_patching_with_pytest_plugin(self, pytest_plugin_enabled):
        """Test that ddtrace.auto doesn't import sitecustomize when pytest plugin is enabled."""
        # Create a fake pytest module with config
        fake_pytest = type(sys)('pytest')
        fake_pytest.config = mock.MagicMock()
        
        with mock.patch.dict('sys.modules', {'pytest': fake_pytest}), \
             mock.patch('ddtrace.bootstrap.sitecustomize') as mock_sitecustomize, \
             mock.patch('ddtrace.contrib.internal.pytest.plugin.is_enabled', 
                       return_value=pytest_plugin_enabled) as mock_is_enabled:
            
            # Import auto module
            import ddtrace.auto  # noqa: F401
            
            # Verify is_enabled was called with the config
            mock_is_enabled.assert_called_once_with(fake_pytest.config)
            
            # Verify sitecustomize was not imported when plugin is enabled
            if pytest_plugin_enabled:
                mock_sitecustomize.assert_not_called()
            else:
                mock_sitecustomize.assert_called_once()
