"""
Tests for exposure event reporting in DataDogProvider.
"""

from unittest import mock

from openfeature.evaluation_context import EvaluationContext
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._ffe_mock import AssignmentReason
from ddtrace.internal.openfeature._ffe_mock import VariationType
from ddtrace.internal.openfeature._ffe_mock import mock_process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.utils import override_global_config


@pytest.fixture
def provider():
    """Create a DataDogProvider instance for testing."""
    with override_global_config({"experimental_flagging_provider_enabled": True}):
        yield DataDogProvider()


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
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                    "variation_key": "on",
                    "reason": AssignmentReason.STATIC.value,
                }
            }
        }
        mock_process_ffe_configuration(config)

        # Resolve flag
        result = provider.resolve_boolean_details("test-flag", False, evaluation_context)

        # Verify flag resolved successfully
        assert result.value is True

        # Verify exposure event was enqueued
        mock_writer.enqueue.assert_called_once()

        # Verify exposure event structure
        exposure_event = mock_writer.enqueue.call_args[0][0]
        assert exposure_event["flag"]["key"] == "test-flag"
        assert exposure_event["variant"]["key"] == "on"
        assert exposure_event["allocation"]["key"] == "on"
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

        config = {
            "flags": {
                "disabled-flag": {
                    "enabled": False,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                }
            }
        }
        mock_process_ffe_configuration(config)

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

        config = {
            "flags": {
                "string-flag": {
                    "enabled": True,
                    "variationType": VariationType.STRING.value,
                    "variations": {
                        "hello": {"key": "hello", "value": "hello"},
                        "world": {"key": "world", "value": "world"},
                    },
                }
            }
        }
        mock_process_ffe_configuration(config)

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

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                    "variation_key": "on",
                }
            }
        }
        mock_process_ffe_configuration(config)

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
        config = {
            "flags": {
                "string-flag": {
                    "enabled": True,
                    "variationType": VariationType.STRING.value,
                    "variations": {"a": {"key": "a", "value": "variant-a"}, "b": {"key": "b", "value": "variant-b"}},
                    "variation_key": "a",
                }
            }
        }
        mock_process_ffe_configuration(config)

        provider.resolve_string_details("string-flag", "default", evaluation_context)

        assert mock_writer.enqueue.call_count == 1
        exposure_event = mock_writer.enqueue.call_args[0][0]
        assert exposure_event["flag"]["key"] == "string-flag"
        assert exposure_event["variant"]["key"] == "a"

    @mock.patch("ddtrace.internal.openfeature.writer.get_exposure_writer")
    def test_exposure_reporting_failure_does_not_affect_resolution(self, mock_get_writer, provider, evaluation_context):
        """Test that exposure reporting failure doesn't break flag resolution."""
        # Make writer raise an exception
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = Exception("Writer error")
        mock_get_writer.return_value = mock_writer

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                    "variation_key": "on",
                }
            }
        }
        mock_process_ffe_configuration(config)

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

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                    "variation_key": "on",
                }
            }
        }
        mock_process_ffe_configuration(config)

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

        config = {
            "flags": {
                "test-flag": {
                    "enabled": True,
                    "variationType": VariationType.STRING.value,
                    "variations": {
                        "success": {"key": "success", "value": "success"},
                        "failure": {"key": "failure", "value": "failure"},
                    },
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_string_details("test-flag", "default", evaluation_context)

        assert result.value == "success"

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_network_error(self, mock_get_writer, provider, evaluation_context):
        """Test that flag resolution continues with network errors."""
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = OSError("Network is unreachable")
        mock_get_writer.return_value = mock_writer

        config = {
            "flags": {
                "network-flag": {
                    "enabled": True,
                    "variationType": VariationType.INTEGER.value,
                    "variations": {"default": {"key": "default", "value": 42}},
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_integer_details("network-flag", 0, evaluation_context)

        assert result.value == 42

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_buffer_full(self, mock_get_writer, provider, evaluation_context):
        """Test handling when exposure writer buffer is full."""
        mock_writer = mock.Mock()
        mock_writer.enqueue.side_effect = Exception("Buffer full")
        mock_get_writer.return_value = mock_writer

        config = {
            "flags": {
                "buffer-flag": {
                    "enabled": True,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                }
            }
        }
        mock_process_ffe_configuration(config)

        # Multiple evaluations should all succeed
        for _ in range(10):
            result = provider.resolve_boolean_details("buffer-flag", False, evaluation_context)
            assert result.value is True

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_writer_returns_none(self, mock_get_writer, provider, evaluation_context):
        """Test handling when get_exposure_writer returns None."""
        mock_get_writer.return_value = None

        config = {
            "flags": {
                "none-writer-flag": {
                    "enabled": True,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                }
            }
        }
        mock_process_ffe_configuration(config)

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

        config = {
            "flags": {
                "intermittent-flag": {
                    "enabled": True,
                    "variationType": VariationType.STRING.value,
                    "variations": {
                        "stable": {"key": "stable", "value": "stable"},
                        "unstable": {"key": "unstable", "value": "unstable"},
                    },
                }
            }
        }
        mock_process_ffe_configuration(config)

        # Multiple evaluations should all succeed despite intermittent failures
        for _ in range(5):
            result = provider.resolve_string_details("intermittent-flag", "default", evaluation_context)
            assert result.value == "stable"

    @mock.patch("ddtrace.internal.openfeature._provider.get_exposure_writer")
    def test_exposure_build_event_returns_none(self, mock_get_writer, provider):
        """Test when build_exposure_event returns None (e.g., missing targeting_key)."""
        mock_writer = mock.Mock()
        mock_get_writer.return_value = mock_writer

        config = {
            "flags": {
                "no-context-flag": {
                    "enabled": True,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                    "variation_key": "on",
                }
            }
        }
        mock_process_ffe_configuration(config)

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

        config = {
            "flags": {
                "exception-flag": {
                    "enabled": True,
                    "variationType": VariationType.BOOLEAN.value,
                    "variations": {"true": {"key": "true", "value": True}, "false": {"key": "false", "value": False}},
                    "variation_key": "on",
                }
            }
        }
        mock_process_ffe_configuration(config)

        result = provider.resolve_boolean_details("exception-flag", False, evaluation_context)

        # Flag should resolve successfully despite exception
        assert result.value is True
        # Verify writer.enqueue was called (even though it raised)
        mock_writer.enqueue.assert_called()
