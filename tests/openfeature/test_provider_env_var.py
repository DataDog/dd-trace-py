"""
Tests for DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED configuration.
"""

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import Reason
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._ffe_mock import AssignmentReason
from ddtrace.internal.openfeature._ffe_mock import VariationType
from ddtrace.internal.openfeature._ffe_mock import mock_process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def clear_config():
    """Clear FFE configuration before and after each test."""
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


class TestProviderConfigEnabled:
    """Test experimental_flagging_provider_enabled=True behavior."""

    def test_provider_enabled_resolves_flags(self):
        """Provider should resolve flags when enabled."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()

            config = {
                "flags": {
                    "test-flag": {
                        "enabled": True,
                        "variationType": VariationType.BOOLEAN.value,
                        "variations": {
                            "true": {"key": "true", "value": True},
                            "false": {"key": "false", "value": False},
                        },
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

    def test_provider_enabled_with_true_value(self):
        """Provider should be enabled when set to True."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()

            config = {
                "flags": {
                    "test-flag": {
                        "enabled": True,
                        "variationType": VariationType.STRING.value,
                        "variations": {
                            "test": {"key": "test", "value": "test-value"},
                            "default": {"key": "default", "value": "default-value"},
                        },
                    }
                }
            }
            mock_process_ffe_configuration(config)

            result = provider.resolve_string_details("test-flag", "default")

            assert result.value == "test-value"
            assert result.reason != Reason.DISABLED


class TestProviderConfigDisabled:
    """Test experimental_flagging_provider_enabled=False or unset behavior."""

    def test_provider_disabled_returns_default(self):
        """Provider should return default values when disabled."""
        with override_global_config({"experimental_flagging_provider_enabled": False}):
            provider = DataDogProvider()

            config = {
                "flags": {
                    "test-flag": {
                        "enabled": True,
                        "variationType": VariationType.BOOLEAN.value,
                        "variations": {
                            "true": {"key": "true", "value": True},
                            "false": {"key": "false", "value": False},
                        },
                    }
                }
            }
            mock_process_ffe_configuration(config)

            result = provider.resolve_boolean_details("test-flag", False)

            assert result.value is False  # default value
            assert result.reason == Reason.DISABLED
            assert result.variant is None

    def test_provider_disabled_by_default(self):
        """Provider should be disabled by default."""
        # Don't override config, use defaults
        provider = DataDogProvider()

        result = provider.resolve_string_details("test-flag", "default-value")

        assert result.value == "default-value"
        assert result.reason == Reason.DISABLED

    def test_provider_disabled_all_types(self):
        """Provider should return defaults for all flag types when disabled."""
        with override_global_config({"experimental_flagging_provider_enabled": False}):
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

    def test_provider_disabled_skips_initialization(self):
        """Provider should skip initialization when disabled."""
        with override_global_config({"experimental_flagging_provider_enabled": False}):
            provider = DataDogProvider()
            context = EvaluationContext(targeting_key="user-123")

            # Should not raise, just skip initialization
            provider.initialize(context)

            # Provider should still return disabled results
            result = provider.resolve_boolean_details("test-flag", True)
            assert result.reason == Reason.DISABLED

    def test_provider_disabled_skips_shutdown(self):
        """Provider should skip shutdown when disabled."""
        with override_global_config({"experimental_flagging_provider_enabled": False}):
            provider = DataDogProvider()

            # Should not raise, just skip shutdown
            provider.shutdown()

    def test_provider_disabled_logs_warning(self):
        """Provider should log an error when disabled."""
        from unittest.mock import patch

        with override_global_config({"experimental_flagging_provider_enabled": False}):
            # Mock the logger to verify error is logged
            with patch("ddtrace.internal.openfeature._provider.logger") as mock_logger:
                _ = DataDogProvider()

                mock_logger.warning.assert_called_once()
                call_args = mock_logger.warning.call_args
                assert "experimental flagging provider is not enabled" in call_args[0][0]
                assert "DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED" in call_args[0][0]
