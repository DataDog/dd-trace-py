"""
Tests for OpenFeature Client API integration with DataDog provider.

Tests the high-level OpenFeature client methods:
- get_boolean_value, get_string_value, get_integer_value, get_float_value, get_object_value
- set_evaluation_context (global context)
- Event handlers (PROVIDER_READY, PROVIDER_ERROR, etc.)
"""

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.event import ProviderEvent
import pytest

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
def setup_provider():
    """Setup DataDog provider and OpenFeature API."""
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider = DataDogProvider()
        api.set_provider(provider)
        yield
        # Cleanup
        api.clear_providers()


@pytest.fixture
def client(setup_provider):
    """Get OpenFeature client."""
    return api.get_client()


@pytest.fixture
def flags_with_rules():
    """Create flags with targeting rules."""
    return create_config(
        {
            "key": "feature-rollout",
            "enabled": True,
            "variationType": "BOOLEAN",
            "variations": {
                "true": {"key": "true", "value": True},
                "false": {"key": "false", "value": False},
            },
            "allocations": [
                {
                    "key": "premium-users",
                    "rules": [
                        {
                            "conditions": [
                                {"attribute": "tier", "operator": "ONE_OF", "value": ["premium", "enterprise"]}
                            ]
                        }
                    ],
                    "splits": [{"variationKey": "true", "shards": []}],
                    "doLog": True,
                },
                {
                    "key": "default",
                    "splits": [{"variationKey": "false", "shards": []}],
                    "doLog": True,
                },
            ],
        },
        {
            "key": "max-items",
            "enabled": True,
            "variationType": "INTEGER",
            "variations": {
                "10": {"key": "10", "value": 10},
                "50": {"key": "50", "value": 50},
                "100": {"key": "100", "value": 100},
            },
            "allocations": [
                {
                    "key": "premium-limit",
                    "rules": [
                        {
                            "conditions": [
                                {"attribute": "tier", "operator": "ONE_OF", "value": ["premium", "enterprise"]}
                            ]
                        }
                    ],
                    "splits": [{"variationKey": "100", "shards": []}],
                    "doLog": True,
                },
                {
                    "key": "basic-limit",
                    "rules": [{"conditions": [{"attribute": "tier", "operator": "ONE_OF", "value": ["basic"]}]}],
                    "splits": [{"variationKey": "50", "shards": []}],
                    "doLog": True,
                },
                {
                    "key": "default",
                    "splits": [{"variationKey": "10", "shards": []}],
                    "doLog": True,
                },
            ],
        },
    )


class TestClientGetMethods:
    """Test client.get_*_value methods."""

    def test_get_boolean_value_success(self, client):
        """Test get_boolean_value returns correct boolean."""
        config = create_config(create_boolean_flag("test-bool", enabled=True, default_value=True))
        process_ffe_configuration(config)

        value = client.get_boolean_value("test-bool", False)

        assert value is True

    def test_get_boolean_value_returns_default_on_error(self, client):
        """Test get_boolean_value returns default when flag not found."""
        config = create_config(create_boolean_flag("existing-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        value = client.get_boolean_value("non-existent-flag", False)

        assert value is False

    def test_get_string_value_success(self, client):
        """Test get_string_value returns correct string."""
        config = create_config(create_string_flag("test-string", "variant-a", enabled=True))
        process_ffe_configuration(config)

        value = client.get_string_value("test-string", "default")

        assert value == "variant-a"

    def test_get_string_value_returns_default_on_error(self, client):
        """Test get_string_value returns default when flag not found."""
        value = client.get_string_value("non-existent", "default-value")

        assert value == "default-value"

    def test_get_integer_value_success(self, client):
        """Test get_integer_value returns correct integer."""
        config = create_config(create_integer_flag("test-int", 42, enabled=True))
        process_ffe_configuration(config)

        value = client.get_integer_value("test-int", 0)

        assert value == 42

    def test_get_integer_value_returns_default_on_error(self, client):
        """Test get_integer_value returns default when flag not found."""
        value = client.get_integer_value("non-existent", 99)

        assert value == 99

    def test_get_float_value_success(self, client):
        """Test get_float_value returns correct float."""
        config = create_config(create_float_flag("test-float", 3.14, enabled=True))
        process_ffe_configuration(config)

        value = client.get_float_value("test-float", 0.0)

        assert value == 3.14

    def test_get_float_value_returns_default_on_error(self, client):
        """Test get_float_value returns default when flag not found."""
        value = client.get_float_value("non-existent", 2.71)

        assert value == 2.71

    def test_get_object_value_success(self, client):
        """Test get_object_value returns correct object."""
        test_obj = {"feature": "enabled", "config": {"max": 100}}
        config = create_config(create_json_flag("test-json", test_obj, enabled=True))
        process_ffe_configuration(config)

        value = client.get_object_value("test-json", {})

        assert value == test_obj
        assert value["feature"] == "enabled"
        assert value["config"]["max"] == 100

    def test_get_object_value_returns_default_on_error(self, client):
        """Test get_object_value returns default when flag not found."""
        default_obj = {"status": "default"}
        value = client.get_object_value("non-existent", default_obj)

        assert value == default_obj


class TestGlobalEvaluationContext:
    """Test global evaluation context functionality."""

    def test_global_context_applied_to_evaluation(self, client, flags_with_rules):
        """Test that global context is used in flag evaluation."""
        process_ffe_configuration(flags_with_rules)

        # Set global context with premium tier
        global_context = EvaluationContext(targeting_key="user-global", attributes={"tier": "premium"})
        api.set_evaluation_context(global_context)

        # Should get premium values without passing context
        bool_value = client.get_boolean_value("feature-rollout", False)
        int_value = client.get_integer_value("max-items", 0)

        assert bool_value is True  # Premium users get feature
        assert int_value == 100  # Premium users get 100 items

    def test_invocation_context_overrides_global(self, client, flags_with_rules):
        """Test that invocation context overrides global context."""
        process_ffe_configuration(flags_with_rules)

        # Set global context with basic tier
        global_context = EvaluationContext(targeting_key="user-global", attributes={"tier": "basic"})
        api.set_evaluation_context(global_context)

        # Override with premium tier in invocation
        invocation_context = EvaluationContext(targeting_key="user-premium", attributes={"tier": "premium"})

        bool_value = client.get_boolean_value("feature-rollout", False, invocation_context)
        int_value = client.get_integer_value("max-items", 0, invocation_context)

        # Should use invocation context (premium), not global (basic)
        assert bool_value is True
        assert int_value == 100

    def test_global_context_with_no_attributes(self, client):
        """Test global context with no attributes."""
        config = create_config(create_string_flag("test-flag", "value-a", enabled=True))
        process_ffe_configuration(config)

        # Set global context with only targeting key
        global_context = EvaluationContext(targeting_key="user-123")
        api.set_evaluation_context(global_context)

        value = client.get_string_value("test-flag", "default")

        assert value == "value-a"

    def test_clearing_global_context(self, client, flags_with_rules):
        """Test clearing global evaluation context."""
        process_ffe_configuration(flags_with_rules)

        # Set global context
        global_context = EvaluationContext(targeting_key="user-global", attributes={"tier": "premium"})
        api.set_evaluation_context(global_context)

        # Verify it works
        value1 = client.get_integer_value("max-items", 0)
        assert value1 == 100

        # Clear global context
        api.set_evaluation_context(EvaluationContext())

        # Should now get default allocation (no tier attribute)
        value2 = client.get_integer_value("max-items", 0)
        assert value2 == 10  # Default allocation

    def test_multiple_clients_share_global_context(self, setup_provider, flags_with_rules):
        """Test that multiple clients share the same global context."""
        process_ffe_configuration(flags_with_rules)

        client1 = api.get_client("client1")
        client2 = api.get_client("client2")

        # Set global context
        global_context = EvaluationContext(targeting_key="shared-user", attributes={"tier": "enterprise"})
        api.set_evaluation_context(global_context)

        # Both clients should use the same global context
        value1 = client1.get_integer_value("max-items", 0)
        value2 = client2.get_integer_value("max-items", 0)

        assert value1 == 100
        assert value2 == 100


class TestProviderEvents:
    """Test provider event handlers."""

    def test_add_and_remove_event_handler(self):
        """Test adding and removing event handlers."""
        handler_calls = []

        def handler(event_details):
            handler_calls.append(event_details)

        # Test adding handler
        api.add_handler(ProviderEvent.PROVIDER_READY, handler)

        try:
            # Verify handler was added (no exception)
            pass
        finally:
            # Test removing handler
            api.remove_handler(ProviderEvent.PROVIDER_READY, handler)

    def test_multiple_event_handlers_can_be_registered(self):
        """Test that multiple handlers can be registered for the same event."""

        def handler1(event_details):
            pass

        def handler2(event_details):
            pass

        api.add_handler(ProviderEvent.PROVIDER_READY, handler1)
        api.add_handler(ProviderEvent.PROVIDER_READY, handler2)

        try:
            # Both handlers should be registered without error
            pass
        finally:
            api.remove_handler(ProviderEvent.PROVIDER_READY, handler1)
            api.remove_handler(ProviderEvent.PROVIDER_READY, handler2)

    def test_provider_error_event_handler(self):
        """Test that PROVIDER_ERROR event handler can be registered."""
        error_calls = []

        def on_error(event_details):
            error_calls.append(event_details)

        api.add_handler(ProviderEvent.PROVIDER_ERROR, on_error)

        try:
            # Handler should be registered without error
            pass
        finally:
            api.remove_handler(ProviderEvent.PROVIDER_ERROR, on_error)


class TestClientWithEvaluationContext:
    """Test client methods with evaluation context parameter."""

    def test_get_boolean_value_with_context(self, client, flags_with_rules):
        """Test get_boolean_value with evaluation context."""
        process_ffe_configuration(flags_with_rules)

        context_premium = EvaluationContext(targeting_key="user1", attributes={"tier": "premium"})
        context_basic = EvaluationContext(targeting_key="user2", attributes={"tier": "basic"})

        value_premium = client.get_boolean_value("feature-rollout", False, context_premium)
        value_basic = client.get_boolean_value("feature-rollout", False, context_basic)

        assert value_premium is True  # Premium gets feature
        assert value_basic is False  # Basic doesn't get feature

    def test_get_integer_value_with_different_contexts(self, client, flags_with_rules):
        """Test get_integer_value with different contexts."""
        process_ffe_configuration(flags_with_rules)

        context_premium = EvaluationContext(targeting_key="user1", attributes={"tier": "premium"})
        context_basic = EvaluationContext(targeting_key="user2", attributes={"tier": "basic"})
        context_free = EvaluationContext(targeting_key="user3", attributes={"tier": "free"})

        value_premium = client.get_integer_value("max-items", 0, context_premium)
        value_basic = client.get_integer_value("max-items", 0, context_basic)
        value_free = client.get_integer_value("max-items", 0, context_free)

        assert value_premium == 100
        assert value_basic == 50
        assert value_free == 10  # Falls through to default allocation

    def test_get_string_value_with_context_targeting_key_only(self, client):
        """Test get_string_value with context containing only targeting key."""
        config = create_config(create_string_flag("test-string", "result", enabled=True))
        process_ffe_configuration(config)

        context = EvaluationContext(targeting_key="user-123")
        value = client.get_string_value("test-string", "default", context)

        assert value == "result"

    def test_get_object_value_with_context(self, client):
        """Test get_object_value with evaluation context."""
        test_config = {"theme": "dark", "items_per_page": 20}
        config = create_config(create_json_flag("ui-config", test_config, enabled=True))
        process_ffe_configuration(config)

        context = EvaluationContext(targeting_key="user-123", attributes={"segment": "beta"})
        value = client.get_object_value("ui-config", {}, context)

        assert value == test_config


class TestClientEdgeCases:
    """Test edge cases and error handling in client API."""

    def test_disabled_flag_returns_default(self, client):
        """Test that disabled flag returns default value."""
        config = create_config(create_string_flag("disabled-flag", "value", enabled=False))
        process_ffe_configuration(config)

        value = client.get_string_value("disabled-flag", "default")

        assert value == "default"

    def test_type_mismatch_returns_default(self, client):
        """Test that type mismatch returns default value."""
        config = create_config(create_string_flag("string-flag", "text", enabled=True))
        process_ffe_configuration(config)

        # Try to get as integer (type mismatch)
        value = client.get_integer_value("string-flag", 99)

        assert value == 99  # Returns default

    def test_empty_flag_key_returns_default(self, client):
        """Test that empty flag key returns default."""
        value = client.get_boolean_value("", True)

        assert value is True

    def test_none_evaluation_context(self, client):
        """Test evaluation with None context."""
        config = create_config(create_string_flag("test-flag", "value", enabled=True))
        process_ffe_configuration(config)

        value = client.get_string_value("test-flag", "default", None)

        assert value == "value"

    def test_special_characters_in_flag_key(self, client):
        """Test flag keys with special characters."""
        config = create_config(create_string_flag("flag-with-special_chars.123", "value", enabled=True))
        process_ffe_configuration(config)

        value = client.get_string_value("flag-with-special_chars.123", "default")

        assert value == "value"


class TestClientWithComplexFlags:
    """Test client API with complex flag configurations."""

    def test_flag_with_multiple_rules(self, client):
        """Test flag with multiple rules (OR logic)."""
        config = create_config(
            {
                "key": "complex-flag",
                "enabled": True,
                "variationType": "STRING",
                "variations": {
                    "variant-a": {"key": "variant-a", "value": "A"},
                    "variant-b": {"key": "variant-b", "value": "B"},
                },
                "allocations": [
                    {
                        "key": "rule1",
                        "rules": [{"conditions": [{"attribute": "country", "operator": "ONE_OF", "value": ["US"]}]}],
                        "splits": [{"variationKey": "variant-a", "shards": []}],
                        "doLog": True,
                    },
                    {
                        "key": "rule2",
                        "rules": [
                            {"conditions": [{"attribute": "email", "operator": "MATCHES", "value": ".*@example.com"}]}
                        ],
                        "splits": [{"variationKey": "variant-a", "shards": []}],
                        "doLog": True,
                    },
                    {
                        "key": "default",
                        "splits": [{"variationKey": "variant-b", "shards": []}],
                        "doLog": True,
                    },
                ],
            }
        )
        process_ffe_configuration(config)

        # Match first rule
        context1 = EvaluationContext(targeting_key="user1", attributes={"country": "US"})
        value1 = client.get_string_value("complex-flag", "default", context1)
        assert value1 == "A"

        # Match second rule
        context2 = EvaluationContext(targeting_key="user2", attributes={"email": "test@example.com"})
        value2 = client.get_string_value("complex-flag", "default", context2)
        assert value2 == "A"

        # Match no rules
        context3 = EvaluationContext(targeting_key="user3", attributes={"country": "UK"})
        value3 = client.get_string_value("complex-flag", "default", context3)
        assert value3 == "B"

    def test_flag_with_numeric_comparisons(self, client):
        """Test flag with numeric comparison operators."""
        config = create_config(
            {
                "key": "age-gate",
                "enabled": True,
                "variationType": "BOOLEAN",
                "variations": {
                    "true": {"key": "true", "value": True},
                    "false": {"key": "false", "value": False},
                },
                "allocations": [
                    {
                        "key": "adult",
                        "rules": [{"conditions": [{"attribute": "age", "operator": "GTE", "value": 18}]}],
                        "splits": [{"variationKey": "true", "shards": []}],
                        "doLog": True,
                    },
                    {
                        "key": "default",
                        "splits": [{"variationKey": "false", "shards": []}],
                        "doLog": True,
                    },
                ],
            }
        )
        process_ffe_configuration(config)

        context_adult = EvaluationContext(targeting_key="user1", attributes={"age": 25})
        context_minor = EvaluationContext(targeting_key="user2", attributes={"age": 15})

        value_adult = client.get_boolean_value("age-gate", False, context_adult)
        value_minor = client.get_boolean_value("age-gate", False, context_minor)

        assert value_adult is True
        assert value_minor is False
