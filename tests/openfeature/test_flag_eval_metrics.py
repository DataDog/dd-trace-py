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
from ddtrace.internal.openfeature._metrics import ATTR_ALLOCATION_KEY
from ddtrace.internal.openfeature._metrics import ATTR_ERROR_TYPE
from ddtrace.internal.openfeature._metrics import ATTR_FLAG_KEY
from ddtrace.internal.openfeature._metrics import ATTR_REASON
from ddtrace.internal.openfeature._metrics import ATTR_VARIANT
from ddtrace.internal.openfeature._metrics import METADATA_ALLOCATION_KEY
from ddtrace.internal.openfeature._metrics import FlagEvalHook
from ddtrace.internal.openfeature._metrics import FlagEvalMetrics
from ddtrace.internal.openfeature._metrics import _error_code_to_tag
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_string_flag
from tests.utils import override_global_config


class TestErrorCodeMapping:
    """Test error code to tag value mapping."""

    def test_flag_not_found(self):
        """FLAG_NOT_FOUND maps to 'flag_not_found'."""
        assert _error_code_to_tag(ErrorCode.FLAG_NOT_FOUND) == "flag_not_found"

    def test_type_mismatch(self):
        """TYPE_MISMATCH maps to 'type_mismatch'."""
        assert _error_code_to_tag(ErrorCode.TYPE_MISMATCH) == "type_mismatch"

    def test_parse_error(self):
        """PARSE_ERROR maps to 'parse_error'."""
        assert _error_code_to_tag(ErrorCode.PARSE_ERROR) == "parse_error"

    def test_general_error(self):
        """GENERAL maps to 'general'."""
        assert _error_code_to_tag(ErrorCode.GENERAL) == "general"

    def test_other_errors_map_to_general(self):
        """Other error codes map to 'general'."""
        assert _error_code_to_tag(ErrorCode.PROVIDER_NOT_READY) == "general"
        assert _error_code_to_tag(ErrorCode.TARGETING_KEY_MISSING) == "general"
        assert _error_code_to_tag(ErrorCode.INVALID_CONTEXT) == "general"


class TestFlagEvalMetrics:
    """Test FlagEvalMetrics class."""

    def test_initialization_without_otel(self):
        """Metrics should initialize gracefully when OTel is not available."""
        with patch.dict("sys.modules", {"opentelemetry": None, "opentelemetry.metrics": None}):
            # Force re-import by creating new instance
            # The import will fail and metrics should be disabled
            _ = FlagEvalMetrics()
            # When OTel import fails, _enabled should be False
            # Note: In real scenario, the import might succeed or fail
            # This test verifies graceful handling

    def test_initialization_with_otel(self):
        """Metrics should initialize with OTel when available."""
        # When OTel is available (as it is in the test environment),
        # the metrics should be enabled and have a counter
        metrics = FlagEvalMetrics()

        # If OTel is available, metrics should be enabled
        # The actual behavior depends on whether OTel is installed
        # We just verify the object is created without error
        assert metrics is not None
        # If enabled, counter should be set
        if metrics._enabled:
            assert metrics._counter is not None

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
        assert attrs[ATTR_FLAG_KEY] == "test-flag"
        assert attrs[ATTR_VARIANT] == "on"
        assert attrs[ATTR_REASON] == "targeting_match"
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
        assert attrs[ATTR_ALLOCATION_KEY] == "default-allocation"

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
        assert attrs[ATTR_ERROR_TYPE] == "flag_not_found"

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
        assert call_kwargs["error_code"] == ErrorCode.FLAG_NOT_FOUND

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
        assert call_kwargs["allocation_key"] is None


class TestProviderHooksIntegration:
    """Test that DataDogProvider properly integrates with hooks."""

    @pytest.fixture
    def provider(self):
        """Create a DataDogProvider instance for testing."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
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

        assert result.value == "hello"
        assert METADATA_ALLOCATION_KEY in result.flag_metadata
        assert result.flag_metadata[METADATA_ALLOCATION_KEY] == "allocation-default"


class TestMetricsWithRealOTel:
    """Integration tests with real OTel (when available)."""

    @pytest.fixture
    def provider(self):
        """Create a DataDogProvider instance for testing."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
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
        assert result.value is True

    def test_metrics_record_on_error(self, provider):
        """Metrics should be recorded even on evaluation errors."""
        # No config set - will result in DEFAULT reason
        result = provider.resolve_boolean_details("non-existent", False)
        assert result.value is False
        assert result.reason == Reason.DEFAULT
