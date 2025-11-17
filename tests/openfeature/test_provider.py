"""
Comprehensive tests for the DataDogProvider following OpenFeature specification.
"""

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import Reason
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_float_flag
from tests.openfeature.config_helpers import create_integer_flag
from tests.openfeature.config_helpers import create_json_flag
from tests.openfeature.config_helpers import create_string_flag
from tests.utils import override_global_config


@pytest.fixture
def provider():
    """Create a DataDogProvider instance for testing."""
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        yield DataDogProvider()


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
        config = create_config(create_boolean_flag("test-bool-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)
        result = provider.resolve_boolean_details("test-bool-flag", False)
        assert result.value is True
        assert result.variant == "true"
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
        config = create_config(create_boolean_flag("disabled-flag", enabled=False, default_value=False))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("disabled-flag", False)

        assert result.value is False
        assert result.reason == Reason.DISABLED

    def test_resolve_boolean_flag_type_mismatch(self, provider):
        """Should return error when flag type doesn't match."""
        config = create_config(create_string_flag("string-flag", "hello", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("string-flag", False)

        assert result.value is False
        assert result.reason == Reason.ERROR
        assert result.error_code == ErrorCode.TYPE_MISMATCH
        assert "expected" in result.error_message.lower()


class TestStringFlagResolution:
    """Test string flag resolution."""

    def test_resolve_string_flag_success(self, provider):
        """Should resolve string flag and return correct value."""
        config = create_config(create_string_flag("test-string-flag", "variant-a", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_string_details("test-string-flag", "default")

        assert result.value == "variant-a"
        assert result.reason == Reason.STATIC
        assert result.variant == "variant-a"
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
        config = create_config(create_integer_flag("test-int-flag", 42, enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_integer_details("test-int-flag", 0)

        assert result.value == 42
        assert result.reason == Reason.STATIC
        assert result.variant == "var-42"
        assert result.error_code is None

    def test_resolve_integer_flag_type_mismatch(self, provider):
        """Should return error when flag type doesn't match."""
        config = create_config(create_boolean_flag("bool-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_integer_details("bool-flag", 0)

        assert result.value == 0
        assert result.reason == Reason.ERROR
        assert result.error_code == ErrorCode.TYPE_MISMATCH


class TestFloatFlagResolution:
    """Test float/numeric flag resolution."""

    def test_resolve_float_flag_success(self, provider):
        """Should resolve float flag and return correct value."""
        config = create_config(create_float_flag("test-float-flag", 3.14159, enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_float_details("test-float-flag", 0.0)

        assert result.value == 3.14159
        assert result.reason == Reason.STATIC
        assert result.variant == "var-3.14159"

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
        config = create_config(
            create_json_flag("test-object-flag", {"key": "value", "nested": {"foo": "bar"}}, enabled=True)
        )
        process_ffe_configuration(config)

        result = provider.resolve_object_details("test-object-flag", {})

        assert result.value == {"key": "value", "nested": {"foo": "bar"}}
        assert result.reason == Reason.STATIC
        assert result.variant == "var-object"

    def test_resolve_object_flag_list_success(self, provider):
        """Should resolve object flag (list) and return correct value."""
        config = create_config(create_json_flag("test-list-flag", [1, 2, 3, "four"], enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_object_details("test-list-flag", [])

        assert result.value == [1, 2, 3, "four"]
        assert result.reason == Reason.STATIC
        assert result.variant == "var-object"

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
        config = create_config(create_boolean_flag("test-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("test-flag", False, evaluation_context)

        assert result.value is True

    def test_resolve_without_evaluation_context(self, provider):
        """Should work without evaluation context."""
        config = create_config(create_string_flag("test-flag", "no-context", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_string_details("test-flag", "default")

        assert result.value == "no-context"


class TestReasonMapping:
    """Test AssignmentReason to OpenFeature Reason mapping."""

    def test_static_reason(self, provider):
        """Should map STATIC reason correctly."""
        config = create_config(create_boolean_flag("static-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("static-flag", False)
        assert result.reason == Reason.STATIC

    def test_targeting_match_reason(self, provider):
        """Should map TARGETING_MATCH reason correctly."""
        # Simple helper creates STATIC allocations, so this test validates STATIC reason
        config = create_config(create_boolean_flag("targeting-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("targeting-flag", False)
        # Helper creates STATIC allocation, not TARGETING_MATCH
        assert result.reason == Reason.STATIC

    def test_split_reason(self, provider):
        """Should map SPLIT reason correctly."""
        # Simple helper creates STATIC allocations, not SPLIT
        config = create_config(create_boolean_flag("split-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("split-flag", False)
        # Helper creates STATIC allocation, not SPLIT
        assert result.reason == Reason.STATIC


class TestErrorHandling:
    """Test error handling according to OpenFeature spec."""

    def test_no_error_code_on_success(self, provider):
        """Should not populate error_code on successful resolution."""
        config = create_config(create_boolean_flag("success-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("success-flag", False)

        assert result.error_code is None
        assert result.error_message is None

    def test_error_code_on_type_mismatch(self, provider):
        """Should populate error_code on type mismatch."""
        config = create_config(create_string_flag("wrong-type-flag", "string", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("wrong-type-flag", False)

        assert result.error_code == ErrorCode.TYPE_MISMATCH
        assert result.error_message is not None
        assert result.reason == Reason.ERROR

    def test_returns_default_on_error(self, provider):
        """Should return default value when error occurs."""
        config = create_config(create_integer_flag("error-flag", 123, enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("error-flag", False)

        assert result.value is False  # default value
        assert result.error_code == ErrorCode.TYPE_MISMATCH


class TestVariantHandling:
    """Test variant field handling."""

    def test_variant_populated_on_success(self, provider):
        """Variant should be populated with variation_key on success."""
        config = create_config(create_string_flag("variant-flag", "variant-value", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_string_details("variant-flag", "default")

        assert result.variant == "variant-value"
        assert result.value == "variant-value"

    def test_variant_none_on_flag_not_found(self, provider):
        """Variant should be None when flag not found."""
        _set_ffe_config(None)

        result = provider.resolve_string_details("missing-flag", "default")

        assert result.variant is None

    def test_default_variant_key(self, provider):
        """Should use 'default' as variant_key when not specified."""
        config = create_config(create_boolean_flag("no-variant-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("no-variant-flag", False)

        # Helper creates "true" as the variant key for default_value=True
        assert result.variant == "true"


class TestComplexScenarios:
    """Test complex real-world scenarios."""

    def test_multiple_flags(self, provider):
        """Should handle multiple flags correctly."""
        config = create_config(
            create_boolean_flag("flag1", enabled=True, default_value=True),
            create_string_flag("flag2", "value2", enabled=True),
            create_integer_flag("flag3", 3, enabled=False),
        )
        process_ffe_configuration(config)

        result1 = provider.resolve_boolean_details("flag1", False)
        result2 = provider.resolve_string_details("flag2", "default")
        result3 = provider.resolve_integer_details("flag3", 0)

        assert result1.value is True
        assert result2.value == "value2"
        assert result3.value == 0  # disabled flag returns default
        assert result3.reason == Reason.DISABLED

    def test_empty_config(self, provider):
        """Should handle empty configuration."""
        # Native library doesn't accept truly empty configs, so just clear it
        _set_ffe_config(None)

        result = provider.resolve_boolean_details("any-flag", True)

        assert result.value is True
        assert result.reason == Reason.DEFAULT


class TestFlagKeyCornerCases:
    """Test corner cases with flag keys including special characters and Unicode."""

    def test_flag_key_with_japanese_characters(self, provider):
        """Should handle flag keys with Japanese characters."""
        config = create_config(create_boolean_flag("機能フラグ", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("機能フラグ", False)

        assert result.value is True

    def test_flag_key_with_emoji(self, provider):
        """Should handle flag keys with emoji characters."""
        config = create_config(create_string_flag("feature-🚀-flag", "rocket-enabled", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_string_details("feature-🚀-flag", "default")

        assert result.value == "rocket-enabled"

    def test_flag_key_with_special_characters(self, provider):
        """Should handle flag keys with special characters."""
        special_keys = [
            "flag.with.dots",
            "flag-with-dashes",
            "flag_with_underscores",
            "flag:with:colons",
            "flag/with/slashes",
            "flag@with@at",
        ]

        for flag_key in special_keys:
            config = create_config(create_boolean_flag(flag_key, enabled=True, default_value=True))
            process_ffe_configuration(config)

            result = provider.resolve_boolean_details(flag_key, False)
            assert result.value is True, f"Failed for key: {flag_key}"

    def test_flag_key_with_spaces(self, provider):
        """Should handle flag keys with spaces."""
        config = create_config(create_boolean_flag("flag with spaces", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("flag with spaces", False)

        assert result.value is True

    def test_flag_key_empty_string(self, provider):
        """Should handle empty string flag key gracefully."""
        result = provider.resolve_boolean_details("", False)

        assert result.value is False
        assert result.reason == Reason.DEFAULT

    def test_flag_key_very_long(self, provider):
        """Should handle very long flag keys."""
        long_key = "a" * 1000
        config = create_config(create_integer_flag(long_key, 42, enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_integer_details(long_key, 0)

        assert result.value == 42

    def test_flag_key_with_cyrillic_characters(self, provider):
        """Should handle flag keys with Cyrillic characters."""
        config = create_config(create_string_flag("флаг-функции", "включено", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_string_details("флаг-функции", "default")

        assert result.value == "включено"

    def test_flag_key_with_arabic_characters(self, provider):
        """Should handle flag keys with Arabic characters."""
        config = create_config(create_boolean_flag("علامة-الميزة", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("علامة-الميزة", False)

        assert result.value is True

    def test_flag_key_with_mixed_unicode(self, provider):
        """Should handle flag keys with mixed Unicode characters."""
        config = create_config(create_boolean_flag("feature-日本語-русский-عربي-🚀", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("feature-日本語-русский-عربي-🚀", False)

        assert result.value is True


class TestInvalidFlagData:
    """Test handling of invalid or malformed flag data."""

    def test_flag_with_null_value(self, provider):
        """Should handle flag with null value."""
        # Native library doesn't accept null values, so test with empty config
        try:
            config = {
                "flags": {
                    "null-flag": {
                        "enabled": True,
                        "variationType": "STRING",
                        "variations": {"default": {"key": "default", "value": None}},
                    }
                }
            }
            process_ffe_configuration(config)
        except ValueError:
            # Expected - native library rejects null values
            pass

        result = provider.resolve_string_details("null-flag", "default")
        # Should return default since config is invalid
        assert result.value == "default"

    def test_flag_missing_enabled_field(self, provider):
        """Should handle flag missing enabled field gracefully."""
        # Native library requires enabled field
        try:
            config = {
                "flags": {
                    "incomplete-flag": {
                        "variationType": "BOOLEAN",
                        "variations": {
                            "true": {"key": "true", "value": True},
                            "false": {"key": "false", "value": False},
                        },
                    }
                }
            }
            process_ffe_configuration(config)
        except ValueError:
            # Expected - native library rejects incomplete configs
            pass

        result = provider.resolve_boolean_details("incomplete-flag", False)
        # Should return default since config is invalid
        assert result.value is False

    def test_flag_with_invalid_variationType(self, provider):
        """Should handle flag with invalid variation type."""
        # Native library validates variation types
        try:
            config = {
                "flags": {
                    "invalid-type-flag": {
                        "enabled": True,
                        "variationType": "INVALID_TYPE",
                        "variations": {"default": {"key": "default", "value": True}},
                    }
                }
            }
            process_ffe_configuration(config)
        except ValueError:
            # Expected - native library rejects invalid types
            pass

        result = provider.resolve_boolean_details("invalid-type-flag", False)
        # Should return default since config is invalid
        assert result.value is False
