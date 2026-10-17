"""
Debug logging of flag evaluations for Feature Flagging and Experimentation (FFE).

Lets customers see why an evaluation returned a given value in their own logs
(DD_TRACE_DEBUG=true), even when telemetry cannot reach Datadog.
"""

import logging
import typing

from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def debug_logging_enabled() -> bool:
    """Whether evaluation details would actually be emitted.

    The provider registers the hook only when this is true, so a filtered log
    level (e.g. DD_TRACE_LOG_LEVEL=error) adds no work to the evaluation path.
    """
    return log.isEnabledFor(logging.DEBUG)


class DebugLoggingHook(Hook):
    """Logs evaluation diagnostics, using finally_after so errored evaluations are covered too."""

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        # Values, raw errors and arbitrary metadata can contain sensitive data.
        # Full-data consent for evaluation telemetry does not authorize logging them.
        log.debug(
            "Feature Flags: evaluated %s: variant=%r, reason=%s, error_code=%s",
            hook_context.flag_key,
            details.variant,
            details.reason,
            details.error_code,
        )
