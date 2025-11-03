"""
Comprehensive tests for DataDogProvider using fixture-based test cases.

These tests validate the provider against real flag configurations and expected outcomes
from the Remote Configuration payload structure.
"""

import json
from pathlib import Path

from openfeature.evaluation_context import EvaluationContext
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.utils import override_global_config


# Get fixtures directory path
FIXTURES_DIR = Path(__file__).parent / "fixtures"
FLAGS_CONFIG_PATH = Path(__file__).parent / "flags-v1.json"


def load_flags_config():
    """Load the main flags configuration."""
    with open(FLAGS_CONFIG_PATH, "r") as f:
        return json.load(f)


def load_fixture_test_cases(fixture_file):
    """Load test cases from a fixture file."""
    fixture_path = FIXTURES_DIR / fixture_file
    with open(fixture_path, "r") as f:
        return json.load(f)


def get_all_fixture_files():
    """Get all fixture JSON files."""
    return [f.name for f in FIXTURES_DIR.glob("*.json")]


def variation_type_to_method(provider, variation_type):
    """Map variationType to the corresponding OpenFeature provider method."""
    mapping = {
        "BOOLEAN": provider.resolve_boolean_details,
        "STRING": provider.resolve_string_details,
        "INTEGER": provider.resolve_integer_details,
        "NUMERIC": provider.resolve_float_details,
        "JSON": provider.resolve_object_details,
    }
    return mapping.get(variation_type)


# Load all fixture files and create test parameters
fixture_files = get_all_fixture_files()
all_test_cases = []

for fixture_file in fixture_files:
    try:
        test_cases = load_fixture_test_cases(fixture_file)
        for i, test_case in enumerate(test_cases):
            # Create a unique test ID
            test_id = f"{fixture_file.replace('.json', '')}_{i}_{test_case.get('targetingKey', 'no_key')}"
            all_test_cases.append((fixture_file, test_case, test_id))
    except Exception as e:
        print(f"Warning: Could not load fixture {fixture_file}: {e}")


@pytest.fixture
def provider():
    """Create a DataDogProvider instance for testing."""
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        yield DataDogProvider()


@pytest.fixture(autouse=True)
def clear_config():
    """Clear FFE configuration before and after each test."""
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.fixture(scope="module")
def flags_config():
    """Load flags configuration once for all tests."""
    return load_flags_config()


@pytest.mark.parametrize("fixture_file,test_case,test_id", all_test_cases, ids=[tc[2] for tc in all_test_cases])
def test_fixture_case(provider, flags_config, fixture_file, test_case, test_id):
    """
    Test flag evaluation using fixture test cases.

    Each test case contains:
    - flag: the flag key to evaluate
    - variationType: the type of flag (BOOLEAN, STRING, INTEGER, NUMERIC, JSON)
    - defaultValue: the default value to pass to the resolution method
    - targetingKey: the targeting key for the evaluation context
    - attributes: additional attributes for the evaluation context
    - result: the expected result containing the value
    """
    # Load the flag configuration
    process_ffe_configuration(flags_config)

    # Extract test case parameters
    flag_key = test_case["flag"]
    variation_type = test_case["variationType"]
    default_value = test_case["defaultValue"]
    targeting_key = test_case.get("targetingKey")
    attributes = test_case.get("attributes", {})
    expected_result = test_case["result"]

    # Create evaluation context
    evaluation_context = EvaluationContext(targeting_key=targeting_key, attributes=attributes)

    # Get the appropriate resolution method based on variationType
    resolve_method = variation_type_to_method(provider, variation_type)
    assert resolve_method is not None, f"Unknown variationType: {variation_type}"

    # Resolve the flag
    result = resolve_method(flag_key, default_value, evaluation_context)

    # Assert the result matches expectations
    expected_value = expected_result.get("value")
    assert result.value == expected_value, (
        f"Flag '{flag_key}' with context (targetingKey='{targeting_key}', attributes={attributes}) "
        f"returned {result.value}, expected {expected_value}"
    )


class TestFixtureSpecificCases:
    """Additional tests for specific fixture scenarios."""

    def test_disabled_flag_returns_default(self, provider, flags_config):
        """Test that disabled flags return the default value."""
        process_ffe_configuration(flags_config)

        context = EvaluationContext(targeting_key="user-123")
        result = provider.resolve_integer_details("disabled_flag", 999, context)

        assert result.value == 999

    def test_empty_flag_returns_default(self, provider, flags_config):
        """Test that flags with no variations return the default value."""
        process_ffe_configuration(flags_config)

        context = EvaluationContext(targeting_key="user-123")
        result = provider.resolve_string_details("empty_flag", "default", context)

        assert result.value == "default"

    def test_no_allocations_flag_returns_default(self, provider, flags_config):
        """Test that flags with no allocations return the default value."""
        process_ffe_configuration(flags_config)

        context = EvaluationContext(targeting_key="user-123")
        result = provider.resolve_object_details("no_allocations_flag", {"default": True}, context)

        assert result.value == {"default": True}

    def test_flag_not_found_returns_default(self, provider, flags_config):
        """Test that non-existent flags return the default value."""
        process_ffe_configuration(flags_config)

        context = EvaluationContext(targeting_key="user-123")
        result = provider.resolve_string_details("non-existent-flag", "default", context)

        assert result.value == "default"

    def test_empty_string_flag_value(self, provider, flags_config):
        """Test that empty strings are returned correctly as flag values."""
        process_ffe_configuration(flags_config)

        # Based on empty_string_flag in flags-v1.json
        context = EvaluationContext(targeting_key="user-123", attributes={"country": "US"})
        result = provider.resolve_string_details("empty_string_flag", "default", context)

        assert result.value == ""  # Empty string is a valid value

    def test_special_characters_in_values(self, provider, flags_config):
        """Test that special characters (emoji, unicode) in flag values work correctly."""
        process_ffe_configuration(flags_config)

        context = EvaluationContext(targeting_key="user-special")
        result = provider.resolve_object_details("special-characters", {}, context)

        # Should return one of the variations with special characters
        assert isinstance(result.value, dict)

    def test_numeric_comparator_operators(self, provider, flags_config):
        """Test numeric comparator operators (LT, LTE, GT, GTE)."""
        process_ffe_configuration(flags_config)

        # Small size (LT 10)
        context = EvaluationContext(targeting_key="user1", attributes={"size": 5})
        result = provider.resolve_string_details("comparator-operator-test", "default", context)
        assert result.value == "small"

        # Medium size (GTE 10 and LTE 20)
        context = EvaluationContext(targeting_key="user2", attributes={"size": 15})
        result = provider.resolve_string_details("comparator-operator-test", "default", context)
        assert result.value == "medium"

        # Large size (GT 25)
        context = EvaluationContext(targeting_key="user3", attributes={"size": 30})
        result = provider.resolve_string_details("comparator-operator-test", "default", context)
        assert result.value == "large"

    def test_null_operator(self, provider, flags_config):
        """Test IS_NULL operator."""
        process_ffe_configuration(flags_config)

        # Size is null
        context = EvaluationContext(targeting_key="user1", attributes={})
        result = provider.resolve_string_details("null-operator-test", "default", context)
        # Without 'size' attribute, IS_NULL should match
        assert result.value == "old"

        # Size is not null
        context = EvaluationContext(targeting_key="user2", attributes={"size": 100})
        result = provider.resolve_string_details("null-operator-test", "default", context)
        assert result.value == "new"

    def test_regex_matches_operator(self, provider, flags_config):
        """Test MATCHES operator with regex patterns."""
        process_ffe_configuration(flags_config)

        # Email ending with @example.com
        context = EvaluationContext(targeting_key="user1", attributes={"email": "user@example.com"})
        result = provider.resolve_string_details("regex-flag", "default", context)
        assert result.value == "partial-example"

        # Email ending with @test.com
        context = EvaluationContext(targeting_key="user2", attributes={"email": "admin@test.com"})
        result = provider.resolve_string_details("regex-flag", "default", context)
        assert result.value == "test"

        # Email that doesn't match
        context = EvaluationContext(targeting_key="user3", attributes={"email": "user@other.com"})
        result = provider.resolve_string_details("regex-flag", "default", context)
        assert result.value == "default"

    def test_one_of_operator_with_multiple_values(self, provider, flags_config):
        """Test ONE_OF operator with multiple values in the list."""
        process_ffe_configuration(flags_config)

        # Country is in the list
        for country in ["US", "Canada", "Mexico"]:
            context = EvaluationContext(targeting_key=f"user-{country}", attributes={"country": country})
            result = provider.resolve_boolean_details("kill-switch", False, context)
            assert result.value is True

        # Country not in the list
        context = EvaluationContext(targeting_key="user-uk", attributes={"country": "UK"})
        result = provider.resolve_boolean_details("kill-switch", False, context)
        # Should be off unless age >= 50
        assert result.value is False

    def test_multiple_rules_in_allocation(self, provider, flags_config):
        """Test allocations with multiple rules (OR logic between rules)."""
        process_ffe_configuration(flags_config)

        # First rule matches (country ONE_OF [US, Canada, Mexico])
        context = EvaluationContext(targeting_key="user1", attributes={"country": "US"})
        result = provider.resolve_integer_details("integer-flag", 0, context)
        assert result.value == 3

        # Second rule matches (email MATCHES .*@example.com)
        context = EvaluationContext(targeting_key="user2", attributes={"email": "test@example.com"})
        result = provider.resolve_integer_details("integer-flag", 0, context)
        assert result.value == 3

        # Neither rule matches - should fall through to default allocation
        context = EvaluationContext(targeting_key="user3", attributes={"country": "UK", "email": "test@other.com"})
        result = provider.resolve_integer_details("integer-flag", 0, context)
        # Should fall through to 50/50 split allocation
        assert result.value in [1, 2]

    def test_json_flag_with_complex_objects(self, provider, flags_config):
        """Test JSON flags with complex object values."""
        process_ffe_configuration(flags_config)

        context = EvaluationContext(targeting_key="user-json")
        result = provider.resolve_object_details("json-config-flag", {}, context)

        # Should return one of the variations
        assert isinstance(result.value, dict)
        # Check it has expected structure from one of the variations
        if result.value:  # Not the empty variation
            assert "integer" in result.value or result.value == {}
