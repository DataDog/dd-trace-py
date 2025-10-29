"""
Tests for DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED environment variable.
"""
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import Reason
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._ffe_mock import AssignmentReason
from ddtrace.internal.openfeature._ffe_mock import VariationType
from ddtrace.internal.openfeature._ffe_mock import mock_process_ffe_configuration
from ddtrace.openfeature import DataDogProvider


@pytest.fixture(autouse=True)
def clear_config():
    """Clear FFE configuration before and after each test."""
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


class TestProviderEnvVarEnabled:
    """Test DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED=true behavior."""

    def test_provider_enabled_resolves_flags(self, monkeypatch):
        """Provider should resolve flags when enabled."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "true")

        provider = DataDogProvider()

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                    "variation_key": "on",
                    "reason": AssignmentReason.STATIC.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("test-flag", False)

        assert result.value is True
        assert result.reason == Reason.STATIC
        assert result.variant == "on"

    def test_provider_enabled_with_1(self, monkeypatch):
        """Provider should be enabled when env var is '1'."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "1")

        provider = DataDogProvider()

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variation_type": VariationType.STRING.value,
                    "value": "test-value",
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_string_details("test-flag", "default")

        assert result.value == "test-value"
        assert result.reason != Reason.DISABLED


class TestProviderEnvVarDisabled:
    """Test DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED=false or unset behavior."""

    def test_provider_disabled_returns_default(self, monkeypatch):
        """Provider should return default values when disabled."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "false")

        provider = DataDogProvider()

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("test-flag", False)

        assert result.value is False  # default value
        assert result.reason == Reason.DISABLED
        assert result.variant is None

    def test_provider_disabled_by_default(self, monkeypatch):
        """Provider should be disabled when env var is not set."""
        monkeypatch.delenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", raising=False)

        provider = DataDogProvider()

        result = provider.resolve_string_details("test-flag", "default-value")

        assert result.value == "default-value"
        assert result.reason == Reason.DISABLED

    def test_provider_disabled_all_types(self, monkeypatch):
        """Provider should return defaults for all flag types when disabled."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "false")

        provider = DataDogProvider()

        # Boolean
        bool_result = provider.resolve_boolean_details("bool-flag", False)
        assert bool_result.value is False
        assert bool_result.reason == Reason.DISABLED

        # String
        string_result = provider.resolve_string_details("string-flag", "default")
        assert string_result.value == "default"
        assert string_result.reason == Reason.DISABLED

        # Integer
        int_result = provider.resolve_integer_details("int-flag", 42)
        assert int_result.value == 42
        assert int_result.reason == Reason.DISABLED

        # Float
        float_result = provider.resolve_float_details("float-flag", 3.14)
        assert float_result.value == 3.14
        assert float_result.reason == Reason.DISABLED

        # Object
        object_result = provider.resolve_object_details("object-flag", {"default": True})
        assert object_result.value == {"default": True}
        assert object_result.reason == Reason.DISABLED

    def test_provider_disabled_skips_initialization(self, monkeypatch):
        """Provider should skip initialization when disabled."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "false")

        provider = DataDogProvider()
        context = EvaluationContext(targeting_key="user-123")

        # Should not raise, just skip initialization
        provider.initialize(context)

        # Provider should still return disabled results
        result = provider.resolve_boolean_details("test-flag", True)
        assert result.reason == Reason.DISABLED

    def test_provider_disabled_skips_shutdown(self, monkeypatch):
        """Provider should skip shutdown when disabled."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "false")

        provider = DataDogProvider()

        # Should not raise, just skip shutdown
        provider.shutdown()

    def test_provider_disabled_logs_error(self, monkeypatch):
        """Provider should log an error when disabled."""
        from unittest.mock import patch

        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "false")

        # Mock the logger to verify error is logged
        with patch("ddtrace.internal.openfeature._provider.logger") as mock_logger:
            _ = DataDogProvider()

            # Verify error was logged
            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args
            assert "experimental flagging provider is not enabled" in call_args[0][0]
            assert "DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED" in call_args[0][1]


class TestProviderEnvVarCaseInsensitive:
    """Test that environment variable parsing is case insensitive."""

    def test_provider_enabled_with_True(self, monkeypatch):
        """Provider should be enabled with 'True' (capital T)."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "True")

        provider = DataDogProvider()

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("test-flag", False)

        assert result.value is True
        assert result.reason != Reason.DISABLED

    def test_provider_enabled_with_TRUE(self, monkeypatch):
        """Provider should be enabled with 'TRUE' (all caps)."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "TRUE")

        provider = DataDogProvider()

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("test-flag", False)

        assert result.value is True
        assert result.reason != Reason.DISABLED

    def test_provider_disabled_with_False(self, monkeypatch):
        """Provider should be disabled with 'False' (capital F)."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "False")

        provider = DataDogProvider()

        result = provider.resolve_boolean_details("test-flag", False)

        assert result.reason == Reason.DISABLED

    def test_provider_disabled_with_invalid_value(self, monkeypatch):
        """Provider should be disabled with invalid values."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED", "invalid")

        provider = DataDogProvider()

        result = provider.resolve_boolean_details("test-flag", False)

        assert result.reason == Reason.DISABLED
