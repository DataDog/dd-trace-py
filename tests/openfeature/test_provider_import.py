"""
Tests for DataDogProvider import behavior when openfeature-sdk is not installed.
"""

import sys
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def cleanup_modules():
    """Clean up module cache before and after each test to ensure test isolation."""
    # Save original modules
    original_modules = sys.modules.copy()
    yield
    # Restore original modules after test
    modules_to_remove = [key for key in list(sys.modules.keys()) if key.startswith("ddtrace.openfeature")]
    for module in modules_to_remove:
        sys.modules.pop(module, None)
    # Restore any modules that were removed
    for key, value in original_modules.items():
        if key.startswith("ddtrace.openfeature") and key not in sys.modules:
            sys.modules[key] = value


class TestProviderImportWithoutOpenFeature:
    """Test DataDogProvider behavior when openfeature-sdk is not available."""

    def test_import_provider_without_openfeature(self):
        """
        Test that importing DataDogProvider works even when openfeature-sdk is not installed.
        """
        # Remove openfeature from sys.modules to simulate it not being installed
        modules_to_remove = [
            key for key in sys.modules.keys() if key.startswith("openfeature") or key == "ddtrace.openfeature"
        ]
        removed_modules = {}
        for module in modules_to_remove:
            if module in sys.modules:
                removed_modules[module] = sys.modules.pop(module)

        # Mock the import to raise ImportError for openfeature
        original_import = __builtins__["__import__"]

        def mock_import(name, *args, **kwargs):
            if name.startswith("openfeature"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        try:
            with mock.patch("builtins.__import__", side_effect=mock_import):
                # This should not raise ImportError
                from ddtrace.openfeature import DataDogProvider

                # Importing should succeed
                assert DataDogProvider is not None

        finally:
            # Restore original modules
            sys.modules.update(removed_modules)

    def test_instantiate_provider_without_openfeature_doesnt_crash(self):
        """
        Test that instantiating DataDogProvider doesn't crash when openfeature-sdk is not installed.
        The provider should be a stub that can be instantiated without errors.
        """
        # Remove openfeature from sys.modules to simulate it not being installed
        modules_to_remove = [
            key for key in sys.modules.keys() if key.startswith("openfeature") or key == "ddtrace.openfeature"
        ]
        removed_modules = {}
        for module in modules_to_remove:
            if module in sys.modules:
                removed_modules[module] = sys.modules.pop(module)

        # Mock the import to raise ImportError for openfeature
        original_import = __builtins__["__import__"]

        def mock_import(name, *args, **kwargs):
            if name.startswith("openfeature"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        try:
            with mock.patch("builtins.__import__", side_effect=mock_import):
                from ddtrace.openfeature import DataDogProvider

                # Instantiate the provider - should not raise an exception
                # This tests that the stub provider works and logs an error message
                provider = DataDogProvider()

                # Provider should be instantiated successfully
                assert provider is not None

        finally:
            # Restore original modules
            sys.modules.update(removed_modules)

    def test_provider_with_openfeature_available(self):
        """
        Test that DataDogProvider works normally when openfeature-sdk is installed.
        This test must run in isolation to avoid module pollution.
        """
        # Force re-import to ensure clean state
        import importlib

        # Remove any cached modules
        modules_to_remove = [key for key in list(sys.modules.keys()) if key.startswith("ddtrace.openfeature")]
        for module in modules_to_remove:
            sys.modules.pop(module, None)

        # Now import fresh
        import ddtrace.openfeature

        importlib.reload(ddtrace.openfeature)

        provider = ddtrace.openfeature.DataDogProvider()

        # Should have proper methods when openfeature is available
        assert hasattr(provider, "get_metadata")
        assert hasattr(provider, "initialize")
        assert hasattr(provider, "shutdown")
        assert hasattr(provider, "resolve_boolean_details")
        assert hasattr(provider, "resolve_string_details")
