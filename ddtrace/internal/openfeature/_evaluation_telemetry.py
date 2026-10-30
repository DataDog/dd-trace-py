from __future__ import annotations

import typing

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)
EVALUATION_TELEMETRY_OPT_OUT = "dd_evaluation_telemetry_opt_out"
METADATA_EVALUATION_TELEMETRY_OPT_OUT = "__dd_evaluation_telemetry_opt_out"


def _opted_out(context: typing.Optional[EvaluationContext]) -> bool:
    return (
        context is not None
        and context.attributes is not None
        and context.attributes.get(EVALUATION_TELEMETRY_OPT_OUT) is True
    )


class EvaluationTelemetryHook(Hook):
    """One invocation's gate for Datadog's finally-only telemetry hooks."""

    def __init__(self, hooks: list[Hook]) -> None:
        self._hooks = hooks
        self._resolved = False
        self._suppressed = False
        self._hook_context_was_opted_out = False

    def before(self, hook_context: HookContext, hints: HookHints) -> None:
        self._snapshot(hook_context)

    def _snapshot(self, hook_context: HookContext) -> None:
        self._hook_context_was_opted_out = _opted_out(hook_context.evaluation_context)
        self._suppressed = self._suppressed or self._hook_context_was_opted_out

    def _capture_resolution(self, details: FlagEvaluationDetails[typing.Any]) -> None:
        # The decision travels with this result, including across executors that
        # do not propagate contextvars. A nested direct resolution has its own
        # metadata and cannot overwrite this invocation's state.
        metadata = dict(details.flag_metadata)
        decision = metadata.pop(METADATA_EVALUATION_TELEMETRY_OPT_OUT, None)
        if isinstance(decision, bool):
            details.flag_metadata = metadata
            self._resolved = True
            self._suppressed = decision

    def after(self, hook_context: HookContext, details: FlagEvaluationDetails[typing.Any], hints: HookHints) -> None:
        # Provider after hooks run before application after hooks. Save the
        # decision before an application hook can mutate details or raise.
        self._snapshot(hook_context)
        self._capture_resolution(details)

    def error(self, hook_context: HookContext, exception: Exception, hints: HookHints) -> None:
        # Provider NOT_READY/FATAL skips all before hooks; application before
        # hooks can also fail before our before stage runs. Capture before
        # application error hooks can remove the attribute.
        if not self._resolved:
            self._snapshot(hook_context)

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        # Returned error resolutions skip after hooks but retain their metadata.
        if not self._resolved:
            self._capture_resolution(details)
        # A late opt-out may suppress more data, but a stale pre-hook True must
        # not override a False returned by a before hook to the resolver.
        late_opt_out = _opted_out(hook_context.evaluation_context) and not self._hook_context_was_opted_out
        if self._suppressed or late_opt_out:
            return
        for hook in reversed(self._hooks):
            try:
                hook.finally_after(hook_context, details, hints)
            except Exception:
                logger.warning("OpenFeature evaluation telemetry hook failed", exc_info=True)
