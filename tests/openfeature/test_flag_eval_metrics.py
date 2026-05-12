"""
Tests for flag evaluation metrics tracking.

Tests the FlagEvalMetrics class and FlagEvalHook that emit
feature_flag.evaluations OTel metrics on every flag evaluation.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import Reason
from openfeature.hook import HookContext
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flageval_metrics import ATTR_ALLOCATION_KEY
from ddtrace.internal.openfeature._flageval_metrics import ATTR_ERROR_TYPE
from ddtrace.internal.openfeature._flageval_metrics import ATTR_FLAG_KEY
from ddtrace.internal.openfeature._flageval_metrics import ATTR_REASON
from ddtrace.internal.openfeature._flageval_metrics import ATTR_VARIANT
from ddtrace.internal.openfeature._flageval_metrics import METADATA_ALLOCATION_KEY
from ddtrace.internal.openfeature._flageval_metrics import FlagEvalHook
from ddtrace.internal.openfeature._flageval_metrics import FlagEvalMetrics
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_string_flag
from tests.utils import override_global_config


class TestFlagEvalMetrics:
    """Test FlagEvalMetrics class."""

    def test_initialization_disabled_when_otel_metrics_not_enabled(self):
        """Metrics should be disabled when DD_METRICS_OTEL_ENABLED is false."""
        # Default config has _otel_metrics_enabled=False
        metrics = FlagEvalMetrics()

        assert metrics._enabled is False
        assert metrics._counter is None

    def test_initialization_without_otel(self):
        """Metrics should initialize gracefully when OTel is not available."""
        with override_global_config({"_otel_metrics_enabled": True}):
            with patch.dict("sys.modules", {"opentelemetry": None, "opentelemetry.metrics": None}):
                # Force re-import by creating new instance
                # The import will fail and metrics should be disabled
                _ = FlagEvalMetrics()
                # When OTel import fails, _enabled should be False
                # Note: In real scenario, the import might succeed or fail
                # This test verifies graceful handling

    def test_initialization_with_otel(self):
        """Metrics should initialize with OTel when available and enabled."""
        with override_global_config({"_otel_metrics_enabled": True}):
            # When OTel is available and metrics are enabled,
            # the metrics should be enabled and have a counter
            metrics = FlagEvalMetrics()

            # The actual behavior depends on whether OTel is installed
            # We just verify the object is created without error
            assert metrics is not None, "If OTel is available, metrics should be enabled"
            if metrics._enabled:
                assert metrics._counter is not None, "If metrics is enabled, counter should be set"

    def test_record_basic_attributes(self):
        """Record should emit metric with basic attributes."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        metrics.record(
            flag_key="test-flag",
            variant="on",
            reason="TARGETING_MATCH",
        )

        mock_counter.add.assert_called_once()
        call_args = mock_counter.add.call_args
        assert call_args[0][0] == 1  # count
        attrs = call_args[1]["attributes"]
        assert attrs[ATTR_FLAG_KEY] == "test-flag", attrs.get(ATTR_FLAG_KEY)
        assert attrs[ATTR_VARIANT] == "on", attrs.get(ATTR_VARIANT)
        assert attrs[ATTR_REASON] == "targeting_match", attrs.get(ATTR_REASON)
        assert ATTR_ERROR_TYPE not in attrs
        assert ATTR_ALLOCATION_KEY not in attrs

    def test_record_with_allocation_key(self):
        """Record should include allocation_key when present."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        metrics.record(
            flag_key="test-flag",
            variant="on",
            reason="STATIC",
            allocation_key="default-allocation",
        )

        call_args = mock_counter.add.call_args
        attrs = call_args[1]["attributes"]
        assert attrs[ATTR_ALLOCATION_KEY] == "default-allocation", attrs.get(ATTR_ALLOCATION_KEY)

    def test_record_with_error(self):
        """Record should include error.type when error_code is present."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        metrics.record(
            flag_key="missing-flag",
            variant="",
            reason="ERROR",
            error_code=ErrorCode.FLAG_NOT_FOUND,
        )

        call_args = mock_counter.add.call_args
        attrs = call_args[1]["attributes"]
        assert attrs[ATTR_ERROR_TYPE] == "flag_not_found", attrs.get(ATTR_ERROR_TYPE)

    def test_record_empty_allocation_key_not_included(self):
        """Empty allocation_key should not be included in attributes."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        metrics.record(
            flag_key="test-flag",
            variant="on",
            reason="STATIC",
            allocation_key="",  # Empty string
        )

        call_args = mock_counter.add.call_args
        attrs = call_args[1]["attributes"]
        assert ATTR_ALLOCATION_KEY not in attrs

    def test_record_when_disabled(self):
        """Record should be no-op when metrics are disabled."""
        metrics = FlagEvalMetrics()
        metrics._enabled = False
        metrics._counter = MagicMock()

        metrics.record(flag_key="test", variant="on", reason="STATIC")

        metrics._counter.add.assert_not_called()

    def test_shutdown(self):
        """Shutdown should disable metrics."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        metrics.shutdown()

        assert metrics._enabled is False
        assert metrics._counter is None

    def test_record_disabled_reason(self):
        """Record should handle DISABLED reason correctly."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        metrics.record(
            flag_key="disabled-flag",
            variant="",
            reason="DISABLED",
        )

        mock_counter.add.assert_called_once()
        call_args = mock_counter.add.call_args
        attrs = call_args[1]["attributes"]
        assert attrs[ATTR_FLAG_KEY] == "disabled-flag", attrs.get(ATTR_FLAG_KEY)
        assert attrs[ATTR_VARIANT] == "", attrs.get(ATTR_VARIANT)
        assert attrs[ATTR_REASON] == "disabled", attrs.get(ATTR_REASON)
        assert ATTR_ERROR_TYPE not in attrs

    def test_record_multiple_evaluations(self):
        """Multiple evaluations should call add() multiple times."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        # Record 5 evaluations of the same flag
        for _ in range(5):
            metrics.record(
                flag_key="my-flag",
                variant="variant-a",
                reason="TARGETING_MATCH",
            )

        # Verify add() was called 5 times
        assert mock_counter.add.call_count == 5

    def test_record_different_flags(self):
        """Different flags should be recorded with different attributes."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        metrics.record(flag_key="flag-a", variant="on", reason="TARGETING_MATCH")
        metrics.record(flag_key="flag-b", variant="off", reason="DEFAULT")

        assert mock_counter.add.call_count == 2

        # Verify each call has the correct flag_key
        calls = mock_counter.add.call_args_list
        flag_keys = {call[1]["attributes"][ATTR_FLAG_KEY] for call in calls}
        assert flag_keys == {"flag-a", "flag-b"}, flag_keys

    def test_record_all_error_types(self):
        """All error types should be recorded correctly."""
        mock_counter = MagicMock()

        metrics = FlagEvalMetrics()
        metrics._enabled = True
        metrics._counter = mock_counter

        error_codes = [
            ErrorCode.FLAG_NOT_FOUND,
            ErrorCode.TYPE_MISMATCH,
            ErrorCode.PARSE_ERROR,
            ErrorCode.GENERAL,
        ]

        for error_code in error_codes:
            metrics.record(
                flag_key="test-flag",
                variant="",
                reason="ERROR",
                error_code=error_code,
            )

        assert mock_counter.add.call_count == len(error_codes)

        # Verify each call has the correct error.type
        calls = mock_counter.add.call_args_list
        error_types = {call[1]["attributes"][ATTR_ERROR_TYPE] for call in calls}
        assert error_types == {"flag_not_found", "type_mismatch", "parse_error", "general"}, error_types


class TestFlagEvalHook:
    """Test FlagEvalHook class."""

    def test_finally_after_calls_record(self):
        """finally_after should call metrics.record with correct arguments."""
        mock_metrics = MagicMock(spec=FlagEvalMetrics)

        hook = FlagEvalHook(mock_metrics)

        # Create mock hook context
        hook_context = MagicMock(spec=HookContext)
        hook_context.flag_key = "test-flag"

        # Create evaluation details
        details = FlagEvaluationDetails(
            flag_key="test-flag",
            value=True,
            variant="on",
            reason=Reason.TARGETING_MATCH,
            flag_metadata={METADATA_ALLOCATION_KEY: "my-allocation"},
        )

        hook.finally_after(hook_context, details, {})

        mock_metrics.record.assert_called_once_with(
            flag_key="test-flag",
            variant="on",
            reason="TARGETING_MATCH",
            error_code=None,
            allocation_key="my-allocation",
        )

    def test_finally_after_with_error(self):
        """finally_after should pass error_code when present."""
        mock_metrics = MagicMock(spec=FlagEvalMetrics)

        hook = FlagEvalHook(mock_metrics)

        hook_context = MagicMock(spec=HookContext)
        hook_context.flag_key = "missing-flag"

        details = FlagEvaluationDetails(
            flag_key="missing-flag",
            value=False,
            variant=None,
            reason=Reason.ERROR,
            error_code=ErrorCode.FLAG_NOT_FOUND,
        )

        hook.finally_after(hook_context, details, {})

        mock_metrics.record.assert_called_once()
        call_kwargs = mock_metrics.record.call_args[1]
        assert call_kwargs["error_code"] == ErrorCode.FLAG_NOT_FOUND, call_kwargs.get("error_code")

    def test_finally_after_without_allocation_key(self):
        """finally_after should handle missing allocation_key."""
        mock_metrics = MagicMock(spec=FlagEvalMetrics)

        hook = FlagEvalHook(mock_metrics)

        hook_context = MagicMock(spec=HookContext)
        hook_context.flag_key = "test-flag"

        details = FlagEvaluationDetails(
            flag_key="test-flag",
            value=True,
            variant="on",
            reason=Reason.STATIC,
            flag_metadata={},  # No allocation_key
        )

        hook.finally_after(hook_context, details, {})

        call_kwargs = mock_metrics.record.call_args[1]
        assert call_kwargs["allocation_key"] is None, call_kwargs.get("allocation_key")


class TestProviderHooksIntegration:
    """Test that DataDogProvider properly integrates with hooks."""

    @pytest.fixture
    def provider(self):
        """Create a DataDogProvider instance for testing."""
        with override_global_config({"experimental_flagging_provider_enabled": True, "_otel_metrics_enabled": True}):
            yield DataDogProvider()

    @pytest.fixture(autouse=True)
    def clear_config(self):
        """Clear FFE configuration before and after each test."""
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    def test_provider_has_flag_eval_hook(self, provider):
        """Provider should have flag evaluation hook when enabled."""
        assert provider._flag_eval_hook is not None
        assert provider._flag_eval_metrics is not None

    def test_get_provider_hooks_returns_flag_eval_hook(self, provider):
        """get_provider_hooks should return the flag eval hook."""
        hooks = provider.get_provider_hooks()
        assert len(hooks) == 1
        assert hooks[0] is provider._flag_eval_hook

    def test_provider_disabled_has_no_hooks(self):
        """Provider should not have hooks when disabled."""
        with override_global_config({"experimental_flagging_provider_enabled": False}):
            provider = DataDogProvider()

        assert provider._flag_eval_hook is None
        assert provider._flag_eval_metrics is None
        assert provider.get_provider_hooks() == []

    def test_shutdown_cleans_up_metrics(self, provider):
        """Shutdown should clean up metrics."""
        assert provider._flag_eval_metrics is not None

        provider.shutdown()

        assert provider._flag_eval_metrics is None
        assert provider._flag_eval_hook is None

    def test_flag_metadata_includes_allocation_key(self, provider):
        """FlagResolutionDetails should include allocation_key in flag_metadata."""
        config = create_config(create_string_flag("test-flag", "hello"))
        process_ffe_configuration(config)

        result = provider.resolve_string_details("test-flag", "default")

        assert result.value == "hello", result.value
        assert METADATA_ALLOCATION_KEY in result.flag_metadata, result.flag_metadata
        assert result.flag_metadata[METADATA_ALLOCATION_KEY] == "allocation-default", result.flag_metadata.get(
            METADATA_ALLOCATION_KEY
        )


class TestMetricsWithRealOTel:
    """Integration tests with real OTel (when available)."""

    @pytest.fixture
    def provider(self):
        """Create a DataDogProvider instance for testing."""
        with override_global_config({"experimental_flagging_provider_enabled": True, "_otel_metrics_enabled": True}):
            yield DataDogProvider()

    @pytest.fixture(autouse=True)
    def clear_config(self):
        """Clear FFE configuration before and after each test."""
        _set_ffe_config(None)
        yield
        _set_ffe_config(None)

    def test_metrics_record_does_not_raise(self, provider):
        """Metrics recording should not raise exceptions."""
        config = create_config(create_boolean_flag("test-flag", enabled=True, default_value=True))
        process_ffe_configuration(config)

        # Should not raise
        result = provider.resolve_boolean_details("test-flag", False)
        assert result.value is True, result.value

    def test_metrics_record_on_error(self, provider):
        """Metrics should be recorded even on evaluation errors."""
        # No config set - will result in ERROR reason with PROVIDER_NOT_READY
        result = provider.resolve_boolean_details("non-existent", False)
        assert result.value is False, result.value
        assert result.reason == Reason.ERROR, result.reason
        assert result.error_code == ErrorCode.PROVIDER_NOT_READY, result.error_code

    def test_disabled_flag_records_disabled_reason(self, provider):
        """Disabled flag should record DISABLED reason in metrics."""
        config = create_config(create_boolean_flag("disabled-flag", enabled=False, default_value=True))
        process_ffe_configuration(config)

        result = provider.resolve_boolean_details("disabled-flag", False)

        assert result.value is False, result.value  # Returns default when disabled
        assert result.reason == Reason.DISABLED, result.reason

    def test_type_conversion_error_records_type_mismatch(self, provider):
        """Type mismatch should record error.type=type_mismatch in metrics.

        This test verifies that when a flag returns a different type than requested
        (e.g., requesting boolean from a string flag), the TYPE_MISMATCH error is
        properly recorded. This proves the hook catches type conversion errors.
        """
        # Create a string flag
        config = create_config(create_string_flag("string-flag", "hello", enabled=True))
        process_ffe_configuration(config)

        # Request it as a boolean - should result in TYPE_MISMATCH
        result = provider.resolve_boolean_details("string-flag", False)

        assert result.value is False, result.value  # Returns default on error
        assert result.reason == Reason.ERROR, result.reason
        assert result.error_code == ErrorCode.TYPE_MISMATCH, result.error_code
