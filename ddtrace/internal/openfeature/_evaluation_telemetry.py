from contextvars import ContextVar
from contextvars import Token
import typing

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)
EVALUATION_TELEMETRY_OPT_OUT = "dd_evaluation_telemetry_opt_out"


def _opted_out(context: typing.Optional[EvaluationContext]) -> bool:
    return (
        context is not None
        and context.attributes is not None
        and context.attributes.get(EVALUATION_TELEMETRY_OPT_OUT) is True
    )


class EvaluationTelemetryHook(Hook):
    """One invocation's gate for Datadog's finally-only telemetry hooks."""

    def __init__(
        self,
        hooks: list[Hook],
        active: ContextVar[typing.Optional["EvaluationTelemetryHook"]],
    ) -> None:
        self._hooks = hooks
        self._handoff = active
        self._token: typing.Optional[Token[typing.Optional[EvaluationTelemetryHook]]] = None
        self._hook_context: typing.Optional[HookContext] = None
        self._resolved_context: typing.Optional[EvaluationContext] = None
        self._resolved = False
        self._suppressed = False
        self._hook_context_was_opted_out = False

    def before(self, hook_context: HookContext, hints: HookHints) -> None:
        self._hook_context = hook_context
        self._suppressed = _opted_out(hook_context.evaluation_context)
        self._token = self._handoff.set(self)

    def capture_resolution_context(self, context: typing.Optional[EvaluationContext]) -> None:
        # OpenFeature merges before-hook returns into a new context, but does
        # not update HookContext. The resolver's context is authoritative,
        # including a returned False that overrides an earlier True.
        self._resolved = True
        self._resolved_context = context
        self._suppressed = _opted_out(context)
        if self._hook_context is not None:
            self._hook_context_was_opted_out = _opted_out(self._hook_context.evaluation_context)
        # The handoff ends at resolver entry, before native evaluation or any
        # nested calls. A copied context cannot reset the caller's token.
        # finally_after restores that token in the caller's context.
        self._handoff.set(None)

    def error(self, hook_context: HookContext, exception: Exception, hints: HookHints) -> None:
        # Provider NOT_READY/FATAL skips all before hooks; application before
        # hooks can also fail before our before stage runs.
        # Capture before application error hooks can remove the attribute.
        if not self._resolved:
            self._suppressed = self._suppressed or _opted_out(hook_context.evaluation_context)

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        try:
            # A late opt-out can only suppress more data. Do not treat a stale
            # True in HookContext as a new opt-out when a before-hook return
            # already replaced it with False in the final resolver context.
            late_opt_out = _opted_out(self._resolved_context) or (
                _opted_out(hook_context.evaluation_context)
                and (not self._resolved or not self._hook_context_was_opted_out)
            )
            if self._suppressed or late_opt_out:
                return
            for hook in reversed(self._hooks):
                try:
                    hook.finally_after(hook_context, details, hints)
                except Exception:
                    # Preserve the SDK's isolation between individual hooks.
                    logger.warning("OpenFeature evaluation telemetry hook failed", exc_info=True)
        finally:
            self._release()

    def _release(self) -> None:
        if self._token is not None:
            token, self._token = self._token, None
            self._handoff.reset(token)
