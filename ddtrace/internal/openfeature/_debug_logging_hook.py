"""
Debug logging of flag evaluations for Feature Flagging and Experimentation (FFE).

Lets customers see why an evaluation returned a given value in their own logs
(DD_TRACE_DEBUG=true), even when telemetry cannot reach Datadog.
"""

import logging
import typing

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagResolutionDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_safe_logger as get_logger
from ddtrace.internal.openfeature._config import _ConfigurationIdentity


log = get_logger(__name__)


def debug_logging_enabled() -> bool:
    """Whether evaluation details would actually be emitted.

    The provider registers the hook only when this is true, so a filtered log
    level (e.g. DD_TRACE_LOG_LEVEL=error) adds no work to the evaluation path.
    """
    return log.isEnabledFor(logging.DEBUG)


def warn_raw_evaluation_data_logged() -> None:
    """Tell the user that debug logs now carry raw evaluation data.

    Logged through the same logger as the evaluation lines, which is at DEBUG whenever
    this is called, so the rate limiter never drops it for a later provider instance.
    """
    log.warning(
        "Feature Flags: debug logging is enabled, so feature flag logs include raw evaluation data (targeting keys, "
        "evaluation context attributes, flag values, and default values) that may contain personal or sensitive "
        "information. Disable debug logging (DD_TRACE_DEBUG, or a DEBUG DD_TRACE_LOG_LEVEL) when you no longer need it."
    )


def log_provider_resolution(
    flag_key: str,
    flag_type: typing.Any,
    default_value: typing.Any,
    evaluation_context: typing.Optional[EvaluationContext],
    details: FlagResolutionDetails[typing.Any],
    configuration_identity: typing.Optional[_ConfigurationIdentity] = None,
) -> None:
    """Log the inputs the provider resolved against and what it resolved.

    The context here includes changes made by before hooks, which the hook context
    does not, and the result precedes any change the SDK or later hooks make to it.
    """
    if not debug_logging_enabled():
        return
    try:
        log.debug(
            "Feature Flags: provider resolved %s: type=%s, targeting_key=%r, attributes=%r, default_value=%r -> "
            "value=%r, variant=%r, reason=%s, error_code=%s, error_message=%r, flag_metadata=%r, configuration=%r",
            flag_key,
            flag_type,
            getattr(evaluation_context, "targeting_key", None),
            getattr(evaluation_context, "attributes", None),
            default_value,
            details.value,
            details.variant,
            details.reason,
            details.error_code,
            details.error_message,
            details.flag_metadata,
            configuration_identity,
        )
    except Exception:
        # A context property may raise before the record reaches the isolated logger.
        return


class DebugLoggingHook(Hook):
    """Observe SDK details before application finally hooks, including SDK fallbacks."""

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        if not debug_logging_enabled():
            return
        try:
            # The SDK can skip resolution when not ready or when a before hook fails.
            # Keep its input context here; only resolution observes merged hook inputs.
            context = hook_context.evaluation_context
            log.debug(
                "Feature Flags: evaluated %s: value=%r, variant=%r, reason=%s, error_code=%s, error_message=%r, "
                "default_value=%r, flag_metadata=%r, input_targeting_key=%r, input_attributes=%r "
                "(SDK observation before application finally hooks)",
                hook_context.flag_key,
                details.value,
                details.variant,
                details.reason,
                details.error_code,
                details.error_message,
                hook_context.default_value,
                details.flag_metadata,
                getattr(context, "targeting_key", None),
                getattr(context, "attributes", None),
            )
        except Exception:
            # Properties can fail before the arguments reach the isolated logger.
            return
