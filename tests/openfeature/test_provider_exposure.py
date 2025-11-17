"""
Tests for exposure event reporting in DataDogProvider.
"""

from unittest import mock

from openfeature.evaluation_context import EvaluationContext
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_integer_flag
from tests.openfeature.config_helpers import create_string_flag
from tests.utils import override_global_config


@pytest.fixture
def provider():
    """Create a DataDogProvider instance for testing."""
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        provider_instance = DataDogProvider()
        # Ensure exposure cache is cleared for each test
        provider_instance.clear_exposure_cache()
        yield provider_instance
        # Clean up after test
        provider_instance.clear_exposure_cache()


@pytest.fixture
def evaluation_context():
    """Create a sample evaluation context with targeting_key."""
    return EvaluationContext(targeting_key="user-123", attributes={"email": "test@example.com", "tier": "premium"})


@pytest.fixture(autouse=True)
def clear_config():
    """Clear FFE configuration before and after each test."""
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


class TestExposureReporting:
    """Test that exposure events are reported correctly."""

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_reported_on_successful_resolution(self, mock_get_writer, provider, evaluation_context):
        """Test that exposure event is reported on successful flag resolution."""
        # Setup mock writer
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        # Setup flag config
        config = {
            "id": "1",
            "createdAt": "2025-10-30T18:36:06.108540853Z",
            "format": "SERVER",
            "environment": {"name": "staging"},
            "flags": {
                "alberto-flag": {
                    "key": "alberto-flag",
                    "enabled": True,
                    "variationType": "BOOLEAN",
                    "variations": {"false": {"key": "false", "value": True}, "true": {"key": "true", "value": True}},
                    "allocations": [
                        {
                            "key": "ffd4e06b-f2de-45cf-aa19-92cf6c768e61",
                            "rules": [{"conditions": [{"operator": "ONE_OF", "attribute": "a", "value": ["b"]}]}],
                            "startAt": "2025-10-29T15:15:23.936522Z",
                            "endAt": "9999-12-31T23:59:59Z",
                            "splits": [{"variationKey": "true", "shards": []}],
                            "doLog": True,
                        },
                        {
                            "key": "allocation-default",
                            "splits": [{"variationKey": "true", "shards": []}],
                            "doLog": True,
                        },
                    ],
                }
            },
        }
        process_ffe_configuration(config)

        # Resolve flag
        result = provider.resolve_boolean_details("alberto-flag", False, evaluation_context)

        # Verify flag resolved successfully
        assert result.value is True

        # Verify exposure event was enqueued
        mock_writer.enqueue.assert_called_once()

        # Verify exposure event structure
        exposure_event = mock_writer.enqueue.call_args[0][0]
        assert exposure_event["flag"]["key"] == "alberto-flag"
        assert exposure_event["variant"]["key"] == "true"
        assert exposure_event["allocation"]["key"] == "allocation-default"
        assert exposure_event["subject"]["id"] == "user-123"
        assert "timestamp" in exposure_event

    @mock.patch("ddtrace.internal.openfeature.writer.get_exposure_writer")
    def test_no_exposure_on_flag_not_found(self, mock_get_writer, provider, evaluation_context):
        """Test that no exposure event is reported when flag is not found."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        _set_ffe_config(None)

        # Resolve non-existent flag
        result = provider.resolve_boolean_details("non-existent-flag", False, evaluation_context)

        # Verify default value returned
        assert result.value is False

        # Verify no exposure event was reported
        mock_writer.enqueue.assert_not_called()

    @mock.patch("ddtrace.internal.openfeature.writer.get_exposure_writer")
    def test_no_exposure_on_disabled_flag(self, mock_get_writer, provider, evaluation_context):
        """Test that no exposure event is reported when flag is disabled."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        config = create_config(create_boolean_flag("disabled-flag", enabled=False, default_value=False))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("disabled-flag", False, evaluation_context)

        # Verify default value returned
        assert result.value is False

        # Verify no exposure event was reported
        mock_writer.enqueue.assert_not_called()

    @mock.patch("ddtrace.internal.openfeature.writer.get_exposure_writer")
    def test_no_exposure_on_type_mismatch(self, mock_get_writer, provider, evaluation_context):
        """Test that no exposure event is reported on type mismatch error."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        config = create_config(create_string_flag("string-flag", "hello", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("string-flag", False, evaluation_context)

        # Verify error occurred
        assert result.value is False

        # Verify no exposure event was reported
        mock_writer.enqueue.assert_not_called()

    @mock.patch("ddtrace.internal.openfeature.writer.get_exposure_writer")
    def test_no_exposure_without_targeting_key(self, mock_get_writer, provider):
        """Test that no exposure event is reported without targeting_key in context."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        # Context without targeting_key
        context = EvaluationContext(attributes={"email": "test@example.com"})

        config = create_config(create_boolean_flag("test-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("test-flag", False, context)

        # Verify flag resolved successfully
        assert result.value is True

        # Verify no exposure event was reported (missing targeting_key)
        mock_writer.enqueue.assert_not_called()

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_with_different_flag_types(self, mock_get_writer, provider, evaluation_context):
        """Test exposure reporting works for all flag types."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        # Test string flag
        config = create_config(create_string_flag("string-flag", "variant-a", enabled=True))
        process_ffe_configuration(config)

        provider.resolve_string_details("string-flag", "default", evaluation_context)

        assert mock_writer.enqueue.call_count == 1
        exposure_event = mock_writer.enqueue.call_args[0][0]
        assert exposure_event["flag"]["key"] == "string-flag"
        assert exposure_event["variant"]["key"] == "variant-a"

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_cached_on_duplicate_evaluation(self, mock_get_writer, provider, evaluation_context):
        """Test that duplicate exposure events are cached and not reported multiple times."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        config = create_config(create_boolean_flag("cached-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # First evaluation - should report exposure
        result1 = provider.resolve_boolean_details("cached-flag", False, evaluation_context)
        assert result1.value is True
        assert mock_writer.enqueue.call_count == 1

        # Second evaluation - should NOT report exposure (cached)
        result2 = provider.resolve_boolean_details("cached-flag", False, evaluation_context)
        assert result2.value is True
        assert mock_writer.enqueue.call_count == 1  # Still 1, not 2

        # Third evaluation - should NOT report exposure (cached)
        result3 = provider.resolve_boolean_details("cached-flag", False, evaluation_context)
        assert result3.value is True
        assert mock_writer.enqueue.call_count == 1  # Still 1, not 3

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_cache_cleared_on_clear_call(self, mock_get_writer, provider, evaluation_context):
        """Test that clearing the cache allows exposure events to be reported again."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        config = create_config(create_boolean_flag("clear-test-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # First evaluation - should report exposure
        provider.resolve_boolean_details("clear-test-flag", False, evaluation_context)
        assert mock_writer.enqueue.call_count == 1

        # Second evaluation - should NOT report (cached)
        provider.resolve_boolean_details("clear-test-flag", False, evaluation_context)
        assert mock_writer.enqueue.call_count == 1

        # Clear the cache
        provider.clear_exposure_cache()

        # Third evaluation - should report again after cache clear
        provider.resolve_boolean_details("clear-test-flag", False, evaluation_context)
        assert mock_writer.enqueue.call_count == 2

    @mock.patch("ddtrace.internal.openfeature.writer.get_exposure_writer")
    def test_exposure_reporting_failure_does_not_affect_resolution(self, mock_get_writer, provider, evaluation_context):
        """Test that exposure reporting failure doesn't break flag resolution."""
        # Make writer raise an exception
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = Exception("Writer error")
        mock_get_writer.return_value = mock_writer

        config = create_config(create_boolean_flag("test-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Should not raise despite writer error
        result = provider.resolve_boolean_details("test-flag", False, evaluation_context)

        # Verify flag still resolved successfully
        assert result.value is True


class TestExposureConnectionErrors:
    """Test exposure reporting with various connection errors."""

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_connection_timeout(self, mock_get_writer, provider, evaluation_context):
        """Test that flag resolution continues when exposure writer times out."""
        # Setup writer that raises timeout error
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = TimeoutError("Connection timeout")
        mock_get_writer.return_value = mock_writer

        config = create_config(create_boolean_flag("test-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Should not raise despite timeout
        result = provider.resolve_boolean_details("test-flag", False, evaluation_context)

        # Flag resolution should succeed
        assert result.value is True

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_connection_refused(self, mock_get_writer, provider, evaluation_context):
        """Test that flag resolution continues when connection is refused."""
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = ConnectionRefusedError("Connection refused")
        mock_get_writer.return_value = mock_writer

        config = create_config(create_string_flag("test-flag", "success", enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_string_details("test-flag", "default", evaluation_context)

        assert result.value == "success"

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_network_error(self, mock_get_writer, provider, evaluation_context):
        """Test that flag resolution continues with network errors."""
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = OSError("Network is unreachable")
        mock_get_writer.return_value = mock_writer

        config = create_config(create_integer_flag("network-flag", 42, enabled=True))
        process_ffe_configuration(config)

        result = provider.resolve_integer_details("network-flag", 0, evaluation_context)

        assert result.value == 42

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_buffer_full(self, mock_get_writer, provider, evaluation_context):
        """Test handling when exposure writer buffer is full."""
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = Exception("Buffer full")
        mock_get_writer.return_value = mock_writer

        config = create_config(create_boolean_flag("buffer-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Multiple evaluations should all succeed
        for _ in range(10):
            result = provider.resolve_boolean_details("buffer-flag", False, evaluation_context)
            assert result.value is True

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_returns_none(self, mock_get_writer, provider, evaluation_context):
        """Test handling when get_exposure_writer returns None."""
        mock_get_writer.return_value = None

        config = create_config(create_boolean_flag("none-writer-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Should not crash
        result = provider.resolve_boolean_details("none-writer-flag", False, evaluation_context)

        assert result.value is True

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_intermittent_failures(self, mock_get_writer, provider, evaluation_context):
        """Test handling of intermittent exposure writer failures."""
        mock_writer = mock.Mock()
        # Alternate between success and failure
        call_count = [0]

        def side_effect_fn(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                raise ConnectionError("Intermittent failure")

        mock_writer.enqueue.side_effect = side_effect_fn
        mock_get_writer.return_value = mock_writer

        config = create_config(create_string_flag("intermittent-flag", "stable", enabled=True))
        process_ffe_configuration(config)

        # Multiple evaluations should all succeed despite intermittent failures
        for _ in range(5):
            result = provider.resolve_string_details("intermittent-flag", "default", evaluation_context)
            assert result.value == "stable"

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_build_event_returns_none(self, mock_get_writer, provider):
        """Test when build_exposure_event returns None (e.g., missing targeting_key)."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        config = create_config(create_boolean_flag("no-context-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Resolve without evaluation context (no targeting_key)
        result = provider.resolve_boolean_details("no-context-flag", False, None)

        # Flag should still resolve
        assert result.value is True

        # No exposure should be enqueued
        mock_writer.enqueue.assert_not_called()

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_generic_exception(self, mock_get_writer, provider, evaluation_context):
        """Test that generic exceptions in exposure writer are handled gracefully."""
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = Exception("Generic error")
        mock_get_writer.return_value = mock_writer

        config = create_config(create_boolean_flag("exception-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("exception-flag", False, evaluation_context)

        # Flag should resolve successfully despite exception
        assert result.value is True
        # Verify writer.enqueue was called (even though it raised)
        mock_writer.enqueue.assert_called()
