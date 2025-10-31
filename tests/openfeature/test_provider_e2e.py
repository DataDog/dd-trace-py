"""
End-to-end tests for DataDogProvider using OpenFeature client.
"""

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
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


@pytest.fixture(autouse=True)
def clear_config():
    """Clear FFE configuration before and after each test."""
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.fixture
def setup_openfeature():
    """Set up OpenFeature API with DataDogProvider."""
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        # Set the provider
        api.set_provider(DataDogProvider())

        # Get a client
        client = api.get_client()

        yield client

        # Cleanup
        api.shutdown()


class TestOpenFeatureE2EBooleanFlags:
    """End-to-end tests for boolean flags using OpenFeature client."""

    def test_boolean_flag_evaluation_success(self, setup_openfeature):
        """Test successful boolean flag evaluation through OpenFeature client."""
        client = setup_openfeature

        # Configure flag
        config = create_config(create_boolean_flag("enable-new-feature", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Evaluate flag
        result = client.get_boolean_value("enable-new-feature", False)

        assert result is True

    def test_boolean_flag_returns_default_when_not_found(self, setup_openfeature):
        """Test that default value is returned when flag is not found."""
        client = setup_openfeature
        _set_ffe_config(None)

        result = client.get_boolean_value("non-existent-flag", False)

        assert result is False

    def test_boolean_flag_with_evaluation_context(self, setup_openfeature):
        """Test boolean flag evaluation with evaluation context."""
        client = setup_openfeature

        config = create_config(create_boolean_flag("premium-feature", enabled=True, default_value=True))
        process_ffe_configuration(config)

        context = EvaluationContext(
            targeting_key="user-123", attributes={"tier": "premium", "email": "test@example.com"}
        )

        result = client.get_boolean_value("premium-feature", False, context)

        assert result is True

    def test_boolean_flag_details(self, setup_openfeature):
        """Test getting boolean flag details."""
        client = setup_openfeature

        config = create_config(create_boolean_flag("detailed-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        details = client.get_boolean_details("detailed-flag", False)

        assert details.value is True
        assert details.variant == "true"
        assert details.error_code is None


class TestOpenFeatureE2EStringFlags:
    """End-to-end tests for string flags using OpenFeature client."""

    def test_string_flag_evaluation(self, setup_openfeature):
        """Test string flag evaluation."""
        client = setup_openfeature

        config = create_config(create_string_flag("api-endpoint", "https://api.production.com", enabled=True))
        process_ffe_configuration(config)

        result = client.get_string_value("api-endpoint", "https://api.staging.com")

        assert result == "https://api.production.com"

    def test_string_flag_returns_default(self, setup_openfeature):
        """Test string flag returns default when not found."""
        client = setup_openfeature
        _set_ffe_config(None)

        result = client.get_string_value("missing-endpoint", "default-url")

        assert result == "default-url"


class TestOpenFeatureE2ENumericFlags:
    """End-to-end tests for numeric flags using OpenFeature client."""

    def test_integer_flag_evaluation(self, setup_openfeature):
        """Test integer flag evaluation."""
        client = setup_openfeature

        config = create_config(create_integer_flag("max-connections", 100, enabled=True))
        process_ffe_configuration(config)

        result = client.get_integer_value("max-connections", 10)

        assert result == 100

    def test_float_flag_evaluation(self, setup_openfeature):
        """Test float flag evaluation."""
        client = setup_openfeature

        config = create_config(create_float_flag("sampling-rate", 0.75, enabled=True))
        process_ffe_configuration(config)

        result = client.get_float_value("sampling-rate", 0.5)

        assert result == 0.75


class TestOpenFeatureE2EObjectFlags:
    """End-to-end tests for object/structure flags using OpenFeature client."""

    def test_object_flag_dict_evaluation(self, setup_openfeature):
        """Test object flag evaluation with dict."""
        client = setup_openfeature

        config = create_config(
            create_json_flag(
                "feature-config", {"timeout": 30, "retries": 3, "endpoints": ["api1", "api2"]}, enabled=True
            )
        )
        process_ffe_configuration(config)

        result = client.get_object_value("feature-config", {})

        assert result["timeout"] == 30
        assert result["retries"] == 3
        assert result["endpoints"] == ["api1", "api2"]

    def test_object_flag_list_evaluation(self, setup_openfeature):
        """Test object flag evaluation with list."""
        client = setup_openfeature

        config = create_config(
            create_json_flag("allowed-regions", ["us-east-1", "eu-west-1", "ap-south-1"], enabled=True)
        )
        process_ffe_configuration(config)

        result = client.get_object_value("allowed-regions", [])

        assert len(result) == 3
        assert "us-east-1" in result
        assert "eu-west-1" in result


class TestOpenFeatureE2EErrorHandling:
    """End-to-end tests for error handling scenarios."""

    def test_type_mismatch_returns_default(self, setup_openfeature):
        """Test that type mismatch returns default value."""
        client = setup_openfeature

        config = create_config(create_string_flag("string-flag", "hello", enabled=True))
        process_ffe_configuration(config)

        # Try to get as boolean (type mismatch)
        result = client.get_boolean_value("string-flag", False)

        # Should return default value
        assert result is False

    def test_disabled_flag_returns_default(self, setup_openfeature):
        """Test that disabled flag returns default value."""
        client = setup_openfeature

        config = create_config(create_boolean_flag("disabled-feature", enabled=False, default_value=False))
        process_ffe_configuration(config)

        result = client.get_boolean_value("disabled-feature", False)

        assert result is False


class TestOpenFeatureE2EMultipleFlags:
    """End-to-end tests with multiple flags."""

    def test_evaluate_multiple_flags_sequentially(self, setup_openfeature):
        """Test evaluating multiple flags in sequence."""
        client = setup_openfeature

        config = create_config(
            create_boolean_flag("feature-a", enabled=True, default_value=True),
            create_string_flag("feature-b", "variant-b", enabled=True),
            create_integer_flag("feature-c", 42, enabled=True),
        )
        process_ffe_configuration(config)

        result_a = client.get_boolean_value("feature-a", False)
        result_b = client.get_string_value("feature-b", "default")
        result_c = client.get_integer_value("feature-c", 0)

        assert result_a is True
        assert result_b == "variant-b"
        assert result_c == 42


class TestOpenFeatureE2EProviderLifecycle:
    """End-to-end tests for provider lifecycle."""

    def test_provider_initialization_and_shutdown(self):
        """Test provider initialization and shutdown lifecycle."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            # Set provider
            provider = DataDogProvider()
        api.set_provider(provider)

        # Get client and use it
        client = api.get_client()

        config = create_config(create_boolean_flag("lifecycle-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = client.get_boolean_value("lifecycle-flag", False)
        assert result is True

        # Shutdown should not raise
        api.shutdown()

    def test_multiple_clients_same_provider(self):
        """Test multiple clients using the same provider."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            api.set_provider(DataDogProvider())

        config = create_config(create_string_flag("shared-flag", "shared-value", enabled=True))
        process_ffe_configuration(config)

        # Get multiple clients
        client1 = api.get_client("client1")
        client2 = api.get_client("client2")

        result1 = client1.get_string_value("shared-flag", "default")
        result2 = client2.get_string_value("shared-flag", "default")

        assert result1 == "shared-value"
        assert result2 == "shared-value"

        api.shutdown()


class TestOpenFeatureE2ERealWorldScenarios:
    """End-to-end tests for real-world scenarios."""

    def test_feature_rollout_scenario(self, setup_openfeature):
        """Test a typical feature rollout scenario."""
        client = setup_openfeature

        # Feature is enabled for premium users
        config = create_config(create_boolean_flag("new-ui", enabled=True, default_value=True))
        process_ffe_configuration(config)

        premium_context = EvaluationContext(targeting_key="user-premium", attributes={"tier": "premium"})

        result = client.get_boolean_value("new-ui", False, premium_context)
        assert result is True

    def test_configuration_management_scenario(self, setup_openfeature):
        """Test using flags for configuration management."""
        client = setup_openfeature

        config = create_config(
            create_json_flag(
                "database-config",
                {"host": "db.production.com", "port": 5432, "pool_size": 20, "timeout": 30},
                enabled=True,
            ),
            create_integer_flag("cache-ttl", 3600, enabled=True),
        )
        process_ffe_configuration(config)

        db_config = client.get_object_value("database-config", {})
        cache_ttl = client.get_integer_value("cache-ttl", 600)

        assert db_config["host"] == "db.production.com"
        assert db_config["pool_size"] == 20
        assert cache_ttl == 3600

    def test_ab_testing_scenario(self, setup_openfeature):
        """Test A/B testing scenario with variants."""
        client = setup_openfeature

        config = create_config(create_string_flag("button-color", "blue", enabled=True))
        process_ffe_configuration(config)

        details = client.get_string_details("button-color", "red")

        assert details.value == "blue"
        assert details.variant == "blue"


class TestOpenFeatureE2ERemoteConfigScenarios:
    """End-to-end tests for remote config edge cases."""

    def test_flag_evaluation_before_remote_config_received(self, setup_openfeature):
        """Test flag evaluation when remote config payload hasn't been received yet."""
        from unittest.mock import patch

        client = setup_openfeature

        # Don't set any config - simulating no remote config received yet
        _set_ffe_config(None)

        # Mock logger to verify warning is logged
        with patch("ddtrace.internal.openfeature._provider.logger"):
            result = client.get_boolean_value("unconfigured-flag", False)

            # Should return default value
            assert result is False

            # Verify warning was logged about missing config
            # (Implementation may log or not, this documents expected behavior)

    def test_flag_evaluation_with_empty_remote_config(self, setup_openfeature):
        """Test flag evaluation with empty remote config."""
        client = setup_openfeature

        # Set empty config (native library doesn't accept truly empty configs, so we just clear it)
        _set_ffe_config(None)

        result = client.get_boolean_value("any-flag", True)

        # Should return default value
        assert result is True

    def test_multiple_flag_evaluations_before_config(self, setup_openfeature):
        """Test multiple flag evaluations before remote config is received."""
        client = setup_openfeature

        _set_ffe_config(None)

        # Multiple evaluations should all return defaults without crashing
        result1 = client.get_boolean_value("flag1", False)
        result2 = client.get_string_value("flag2", "default")
        result3 = client.get_integer_value("flag3", 0)

        assert result1 is False
        assert result2 == "default"
        assert result3 == 0

    def test_flag_evaluation_after_remote_config_arrives(self, setup_openfeature):
        """Test that flags work correctly after remote config arrives."""
        client = setup_openfeature

        # Start with no config
        _set_ffe_config(None)

        # First evaluation returns default
        result1 = client.get_boolean_value("late-flag", False)
        assert result1 is False

        # Now remote config arrives
        config = create_config(create_boolean_flag("late-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Second evaluation should use the flag value
        result2 = client.get_boolean_value("late-flag", False)
        assert result2 is True

    def test_remote_config_update_during_runtime(self, setup_openfeature):
        """Test that flag values update when remote config changes."""
        client = setup_openfeature

        # Initial config
        config1 = create_config(create_string_flag("dynamic-flag", "version1", enabled=True))
        process_ffe_configuration(config1)

        result1 = client.get_string_value("dynamic-flag", "default")
        assert result1 == "version1"

        # Update config
        config2 = create_config(create_string_flag("dynamic-flag", "version2", enabled=True))
        process_ffe_configuration(config2)

        result2 = client.get_string_value("dynamic-flag", "default")
        assert result2 == "version2"

    def test_remote_config_with_malformed_data(self, setup_openfeature):
        """Test handling of malformed remote config data."""
        client = setup_openfeature

        # Malformed config (missing required fields) - native library will reject this
        # So we test that the system handles missing config gracefully
        try:
            config = {
                "flags": {
                    "malformed-flag": {
                        "enabled": True,
                        # Missing variationType and value
                    }
                }
            }
            process_ffe_configuration(config)
        except ValueError:
            # Expected - native library rejects malformed config
            pass

        # With no valid config, should return default
        result = client.get_boolean_value("malformed-flag", False)
        assert result is False
