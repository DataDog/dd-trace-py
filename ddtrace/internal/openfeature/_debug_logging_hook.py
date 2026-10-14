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
    """Logs full evaluation details, using finally_after so errored evaluations are covered too."""

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        # The details carry the flag key, value, variant, reason, error and flag
        # metadata, but never the evaluation context (targeting key/attributes).
        # Formatting is deferred to the logging framework, which reports its own
        # errors instead of raising, and the SDK guards hooks as well.
        log.debug("Feature Flags: evaluated %s: %r", hook_context.flag_key, details)
