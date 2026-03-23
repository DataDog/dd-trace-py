"""
Flag evaluation metrics for Feature Flagging and Experimentation (FFE).

This module implements OpenTelemetry metrics tracking for feature flag evaluations,
emitting a `feature_flag.evaluations` counter metric on every flag evaluation.
"""

import typing

from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# Metric constants
METER_NAME = "ddtrace.openfeature"
METRIC_NAME = "feature_flag.evaluations"
METRIC_UNIT = "{evaluation}"
METRIC_DESC = "Number of feature flag evaluations"

# Attribute keys (following OTel semconv naming)
ATTR_FLAG_KEY = "feature_flag.key"
ATTR_VARIANT = "feature_flag.result.variant"
ATTR_REASON = "feature_flag.result.reason"
ATTR_ERROR_TYPE = "error.type"
ATTR_ALLOCATION_KEY = "feature_flag.result.allocation_key"

# Metadata key for allocation_key (must match the key used in _native.py)
METADATA_ALLOCATION_KEY = "allocation_key"


class FlagEvalMetrics:
    """
    Manages OTel metric instruments for flag evaluation tracking.

    Creates and maintains an Int64Counter for the feature_flag.evaluations metric.
    If DD_METRICS_OTEL_ENABLED is not true, the counter operations are no-ops.
    """

    def __init__(self) -> None:
        """
        Initialize the flag evaluation metrics.

        Creates an OTel MeterProvider and Int64Counter for tracking flag evaluations.
        Only enabled when DD_METRICS_OTEL_ENABLED=true.
        """
        self._counter: typing.Optional[typing.Any] = None
        self._enabled = False

        # Only create metrics if OTel metrics are enabled
        try:
            from ddtrace import config as ddconfig

            if not ddconfig._otel_metrics_enabled:
                log.debug("OTel metrics not enabled (DD_METRICS_OTEL_ENABLED=false), flag evaluation metrics disabled")
                return
        except ImportError:
            # ddtrace config not available (e.g., in isolated test environments)
            log.debug("ddtrace config not available, flag evaluation metrics disabled")
            return

        try:
            from opentelemetry import metrics as otel_metrics

            # Get the global meter provider (set up by ddtrace OTel metrics infrastructure)
            meter = otel_metrics.get_meter(METER_NAME)

            self._counter = meter.create_counter(
                name=METRIC_NAME,
                unit=METRIC_UNIT,
                description=METRIC_DESC,
            )

            self._enabled = True
            log.debug("Flag evaluation metrics initialized successfully")
        except ImportError:
            log.debug("OpenTelemetry metrics not available, flag evaluation metrics disabled")
        except Exception as e:
            log.debug("Failed to initialize flag evaluation metrics: %s", e)

    def record(
        self,
        flag_key: str,
        variant: typing.Optional[str],
        reason: typing.Optional[str],
        error_code: typing.Optional[ErrorCode] = None,
        allocation_key: typing.Optional[str] = None,
    ) -> None:
        """
        Record a single flag evaluation metric.

        Args:
            flag_key: The feature flag key
            variant: The resolved variant (may be None/empty on error)
            reason: The evaluation reason (lowercased)
            error_code: The error code if evaluation failed (optional)
            allocation_key: The allocation key if an allocation matched (optional)
        """
        if not self._enabled or self._counter is None:
            return

        try:
            attributes: dict[str, str] = {
                ATTR_FLAG_KEY: flag_key,
                ATTR_VARIANT: variant or "",
                ATTR_REASON: (reason or "unknown").lower(),
            }

            # Add error.type attribute only on error
            if error_code is not None:
                attributes[ATTR_ERROR_TYPE] = error_code.value.lower()

            # Add allocation_key only when present and non-empty
            if allocation_key:
                attributes[ATTR_ALLOCATION_KEY] = allocation_key

            self._counter.add(1, attributes=attributes)
        except Exception as e:
            log.debug("Failed to record flag evaluation metric: %s", e)

    def shutdown(self) -> None:
        """
        Shutdown the metrics tracking.

        Note: The actual MeterProvider shutdown is handled by the global
        OTel metrics infrastructure in ddtrace.
        """
        self._enabled = False
        self._counter = None
        log.debug("Flag evaluation metrics shutdown")


class FlagEvalHook(Hook):
    """
    OpenFeature Hook that tracks flag evaluation metrics.

    Implements the finally_after hook stage to record metrics after every
    flag evaluation, including evaluations that result in errors or
    type conversion failures.
    """

    def __init__(self, metrics: FlagEvalMetrics) -> None:
        """
        Initialize the flag evaluation hook.

        Args:
            metrics: The FlagEvalMetrics instance to use for recording
        """
        self._metrics = metrics

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        """
        Called after every flag evaluation (success or error).

        Records a metric for the evaluation result with appropriate attributes.

        Args:
            hook_context: Context information about the flag evaluation
            details: The evaluation details including value, variant, reason, and errors
            hints: Optional hints passed to the hook (unused)
        """
        try:
            # Extract allocation_key from flag_metadata if present
            allocation_key: typing.Optional[str] = None
            if details.flag_metadata:
                ak = details.flag_metadata.get(METADATA_ALLOCATION_KEY)
                if isinstance(ak, str) and ak:
                    allocation_key = ak

            # Get reason as string
            reason_str: typing.Optional[str] = None
            if details.reason is not None:
                # Reason can be an enum or string depending on SDK version
                reason_str = str(details.reason.value) if hasattr(details.reason, "value") else str(details.reason)

            self._metrics.record(
                flag_key=hook_context.flag_key,
                variant=details.variant,
                reason=reason_str,
                error_code=details.error_code,
                allocation_key=allocation_key,
            )
        except (TypeError, AttributeError) as e:
            log.debug("Failed to extract flag evaluation details: %s", e)
