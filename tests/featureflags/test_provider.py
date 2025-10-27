"""
Comprehensive tests for the DataDogProvider following OpenFeature specification.
"""

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import Reason
import pytest

from ddtrace.featureflags import DataDogProvider
from ddtrace.featureflags._config import _set_ffe_config
from ddtrace.featureflags._ffe_mock import AssignmentReason
from ddtrace.featureflags._ffe_mock import VariationType
from ddtrace.featureflags._ffe_mock import mock_process_ffe_configuration


@pytest.fixture
def provider():
    """Create a DataDogProvider instance for testing."""
    return DataDogProvider()


@pytest.fixture
def evaluation_context():
    """Create a sample evaluation context."""
    return EvaluationContext(targeting_key="user-123", attributes={"email": "test@example.com", "tier": "premium"})


@pytest.fixture(autouse=True)
def clear_config():
    """Clear FFE configuration before and after each test."""
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


class TestProviderMetadata:
    """Test provider metadata requirements (OpenFeature Spec 2.1)."""

    def test_provider_has_metadata(self, provider):
        """Provider must define metadata with a name field."""
        metadata = provider.get_metadata()
        assert metadata is not None
        assert metadata.name == "Datadog"


class TestProviderInitializationShutdown:
    """Test provider initialization and shutdown (OpenFeature Spec 2.4, 2.5)."""

    def test_provider_initialization(self, provider, evaluation_context):
        """Provider should initialize without errors."""
        # Should not raise
        provider.initialize(evaluation_context)

    def test_provider_shutdown(self, provider):
        """Provider should shutdown without errors."""
        # Should not raise
        provider.shutdown()


class TestBooleanFlagResolution:
    """Test boolean flag resolution (OpenFeature Spec 2.2)."""

    def test_resolve_boolean_flag_success(self, provider):
        """Should resolve boolean flag and return correct value."""
        config = {
            "flags": {
                "test-bool-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                    "variation_key": "on",
                    "reason": AssignmentReason.STATIC.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("test-bool-flag", False)

        assert result.value is True
        assert result.reason == Reason.STATIC
        assert result.variant == "on"
        assert result.error_code is None
        assert result.error_message is None

    def test_resolve_boolean_flag_not_found(self, provider):
        """Should return default value when flag not found."""
        _set_ffe_config(None)

        result = provider.resolve_boolean_details("non-existent-flag", False)

        assert result.value is False
        assert result.reason == Reason.DEFAULT
        assert result.variant is None
        assert result.error_code is None

    def test_resolve_boolean_flag_disabled(self, provider):
        """Should return default value when flag is disabled."""
        config = {
            "flags": {
                "disabled-flag": {
                    "enabled": False,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("disabled-flag", False)

        assert result.value is False
        assert result.reason == Reason.DEFAULT

    def test_resolve_boolean_flag_type_mismatch(self, provider):
        """Should return error when flag type doesn't match."""
        config = {
            "flags": {
                "string-flag": {
                    "enabled": True,
                    "variation_type": VariationType.STRING.value,
                    "value": "hello",
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("string-flag", False)

        assert result.value is False
        assert result.reason == Reason.ERROR
        assert result.error_code == ErrorCode.TYPE_MISMATCH
        assert "Expected" in result.error_message


class TestStringFlagResolution:
    """Test string flag resolution."""

    def test_resolve_string_flag_success(self, provider):
        """Should resolve string flag and return correct value."""
        config = {
            "flags": {
                "test-string-flag": {
                    "enabled": True,
                    "variation_type": VariationType.STRING.value,
                    "value": "variant-a",
                    "variation_key": "a",
                    "reason": AssignmentReason.TARGETING_MATCH.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_string_details("test-string-flag", "default")

        assert result.value == "variant-a"
        assert result.reason == Reason.TARGETING_MATCH
        assert result.variant == "a"
        assert result.error_code is None

    def test_resolve_string_flag_not_found(self, provider):
        """Should return default value when flag not found."""
        _set_ffe_config(None)

        result = provider.resolve_string_details("non-existent-flag", "default")

        assert result.value == "default"
        assert result.reason == Reason.DEFAULT


class TestIntegerFlagResolution:
    """Test integer flag resolution."""

    def test_resolve_integer_flag_success(self, provider):
        """Should resolve integer flag and return correct value."""
        config = {
            "flags": {
                "test-int-flag": {
                    "enabled": True,
                    "variation_type": VariationType.INTEGER.value,
                    "value": 42,
                    "variation_key": "int-variant",
                    "reason": AssignmentReason.SPLIT.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_integer_details("test-int-flag", 0)

        assert result.value == 42
        assert result.reason == Reason.SPLIT
        assert result.variant == "int-variant"
        assert result.error_code is None

    def test_resolve_integer_flag_type_mismatch(self, provider):
        """Should return error when flag type doesn't match."""
        config = {
            "flags": {
                "bool-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_integer_details("bool-flag", 0)

        assert result.value == 0
        assert result.reason == Reason.ERROR
        assert result.error_code == ErrorCode.TYPE_MISMATCH


class TestFloatFlagResolution:
    """Test float/numeric flag resolution."""

    def test_resolve_float_flag_success(self, provider):
        """Should resolve float flag and return correct value."""
        config = {
            "flags": {
                "test-float-flag": {
                    "enabled": True,
                    "variation_type": VariationType.NUMERIC.value,
                    "value": 3.14159,
                    "variation_key": "pi",
                    "reason": AssignmentReason.STATIC.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_float_details("test-float-flag", 0.0)

        assert result.value == 3.14159
        assert result.reason == Reason.STATIC
        assert result.variant == "pi"

    def test_resolve_float_flag_not_found(self, provider):
        """Should return default value when flag not found."""
        _set_ffe_config(None)

        result = provider.resolve_float_details("non-existent-flag", 1.0)

        assert result.value == 1.0
        assert result.reason == Reason.DEFAULT


class TestObjectFlagResolution:
    """Test object/structure flag resolution."""

    def test_resolve_object_flag_dict_success(self, provider):
        """Should resolve object flag (dict) and return correct value."""
        config = {
            "flags": {
                "test-object-flag": {
                    "enabled": True,
                    "variation_type": VariationType.JSON.value,
                    "value": {"key": "value", "nested": {"foo": "bar"}},
                    "variation_key": "obj-variant",
                    "reason": AssignmentReason.TARGETING_MATCH.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_object_details("test-object-flag", {})

        assert result.value == {"key": "value", "nested": {"foo": "bar"}}
        assert result.reason == Reason.TARGETING_MATCH
        assert result.variant == "obj-variant"

    def test_resolve_object_flag_list_success(self, provider):
        """Should resolve object flag (list) and return correct value."""
        config = {
            "flags": {
                "test-list-flag": {
                    "enabled": True,
                    "variation_type": VariationType.JSON.value,
                    "value": [1, 2, 3, "four"],
                    "variation_key": "list-variant",
                    "reason": AssignmentReason.STATIC.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_object_details("test-list-flag", [])

        assert result.value == [1, 2, 3, "four"]
        assert result.reason == Reason.STATIC
        assert result.variant == "list-variant"

    def test_resolve_object_flag_not_found(self, provider):
        """Should return default value when flag not found."""
        _set_ffe_config(None)

        default = {"default": True}
        result = provider.resolve_object_details("non-existent-flag", default)

        assert result.value == default
        assert result.reason == Reason.DEFAULT


class TestEvaluationContext:
    """Test evaluation context handling."""

    def test_resolve_with_evaluation_context(self, provider, evaluation_context):
        """Should accept evaluation context without errors."""
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

        result = provider.resolve_boolean_details("test-flag", False, evaluation_context)

        assert result.value is True

    def test_resolve_without_evaluation_context(self, provider):
        """Should work without evaluation context."""
        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variation_type": VariationType.STRING.value,
                    "value": "no-context",
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_string_details("test-flag", "default")

        assert result.value == "no-context"


class TestReasonMapping:
    """Test AssignmentReason to OpenFeature Reason mapping."""

    def test_static_reason(self, provider):
        """Should map STATIC reason correctly."""
        config = {
            "flags": {
                "static-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                    "reason": AssignmentReason.STATIC.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("static-flag", False)
        assert result.reason == Reason.STATIC

    def test_targeting_match_reason(self, provider):
        """Should map TARGETING_MATCH reason correctly."""
        config = {
            "flags": {
                "targeting-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                    "reason": AssignmentReason.TARGETING_MATCH.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("targeting-flag", False)
        assert result.reason == Reason.TARGETING_MATCH

    def test_split_reason(self, provider):
        """Should map SPLIT reason correctly."""
        config = {
            "flags": {
                "split-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                    "reason": AssignmentReason.SPLIT.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("split-flag", False)
        assert result.reason == Reason.SPLIT


class TestErrorHandling:
    """Test error handling according to OpenFeature spec."""

    def test_no_error_code_on_success(self, provider):
        """Should not populate error_code on successful resolution."""
        config = {
            "flags": {
                "success-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("success-flag", False)

        assert result.error_code is None
        assert result.error_message is None

    def test_error_code_on_type_mismatch(self, provider):
        """Should populate error_code on type mismatch."""
        config = {
            "flags": {
                "wrong-type-flag": {
                    "enabled": True,
                    "variation_type": VariationType.STRING.value,
                    "value": "string",
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("wrong-type-flag", False)

        assert result.error_code == ErrorCode.TYPE_MISMATCH
        assert result.error_message is not None
        assert result.reason == Reason.ERROR

    def test_returns_default_on_error(self, provider):
        """Should return default value when error occurs."""
        config = {
            "flags": {
                "error-flag": {
                    "enabled": True,
                    "variation_type": VariationType.INTEGER.value,
                    "value": 123,
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("error-flag", False)

        assert result.value is False  # default value
        assert result.error_code == ErrorCode.TYPE_MISMATCH


class TestVariantHandling:
    """Test variant field handling."""

    def test_variant_populated_on_success(self, provider):
        """Variant should be populated with variation_key on success."""
        config = {
            "flags": {
                "variant-flag": {
                    "enabled": True,
                    "variation_type": VariationType.STRING.value,
                    "value": "variant-value",
                    "variation_key": "my-variant-key",
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_string_details("variant-flag", "default")

        assert result.variant == "my-variant-key"
        assert result.value == "variant-value"

    def test_variant_none_on_flag_not_found(self, provider):
        """Variant should be None when flag not found."""
        _set_ffe_config(None)

        result = provider.resolve_string_details("missing-flag", "default")

        assert result.variant is None

    def test_default_variant_key(self, provider):
        """Should use 'default' as variant_key when not specified."""
        config = {
            "flags": {
                "no-variant-flag": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                    # No variation_key specified
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("no-variant-flag", False)

        assert result.variant == "default"


class TestComplexScenarios:
    """Test complex real-world scenarios."""

    def test_multiple_flags(self, provider):
        """Should handle multiple flags correctly."""
        config = {
            "flags": {
                "flag1": {
                    "enabled": True,
                    "variation_type": VariationType.BOOLEAN.value,
                    "value": True,
                },
                "flag2": {
                    "enabled": True,
                    "variation_type": VariationType.STRING.value,
                    "value": "value2",
                },
                "flag3": {
                    "enabled": False,
                    "variation_type": VariationType.INTEGER.value,
                    "value": 3,
                },
            }
        }
        mock_process_ffe_configuration(config)

        result1 = provider.resolve_boolean_details("flag1", False)
        result2 = provider.resolve_string_details("flag2", "default")
        result3 = provider.resolve_integer_details("flag3", 0)

        assert result1.value is True
        assert result2.value == "value2"
        assert result3.value == 0  # disabled flag returns default
        assert result3.reason == Reason.DEFAULT

    def test_empty_config(self, provider):
        """Should handle empty configuration."""
        config = {"flags": {}}
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("any-flag", True)

        assert result.value is True
        assert result.reason == Reason.DEFAULT
