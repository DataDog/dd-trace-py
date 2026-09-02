"""Coordinated sampling for Live Debugger probes.

Every probe used to make its own sampling decision, so the snapshots from a chain
of related calls arrived in pieces, with no signal to the consumer that anything
was missing. Here the decision is made once per unit of execution and inherited
by every probe that fires within it, so a chain either arrives whole or not at
all.

The decision belongs to the debugger, not to the signals it emits: a signal
records what happened, it does not choose whether to happen.
"""

from contextvars import ContextVar
from contextvars import Token
from enum import Enum
from types import FrameType
import typing as t

from ddtrace import tracer
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.model import RateLimitMixin
from ddtrace.debugging._session import Session
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter
from ddtrace.internal.rate_limiter import RateLimitExceeded


class Decision(str, Enum):
    """Outcome of the sampling gate for a single probe firing."""

    FIRE = "FIRE"
    #: The budget was spent when this unit of execution made its decision.
    DROP_SAMPLED = "DROP_SAMPLED"
    #: The unit is emitting, but this probe already emitted here.
    DROP_CAPPED = "DROP_CAPPED"
    #: The probe is not sampled and is over its own rate.
    DROP_RATE = "DROP_RATE"


# AIDEV-NOTE: Two tiers, two stores, because the scopes have different lifetimes.
#
# Tier 1 -- a trace is running. Both the decision and the record of what has
# fired live on the local root span, so one decision covers the whole trace and
# the state dies with it. The cap is keyed on the span the probe fired in.
#
# Tier 2 -- no trace. Both live in contextvars, which follow asyncio tasks for
# free: a task copies the context at creation, so a scope opened inside a task is
# invisible to its parent and siblings. The cap is keyed on the frame the probe
# fired in, which is the closest thing to a span here: it lets a probe in a loop
# fire once for the invocation while still giving the next call a fresh slot.
#
# A contextvar set inside a function does NOT unset when that function returns.
# Left alone it would persist for the life of the thread, pinning every later
# probe to one decision. So the tier 2 store is only ever opened by something
# that also closes it: open_scope/close_scope, called by the wrapping context
# that brackets a probed invocation.
#
# These are module state rather than sampler state on purpose: a scope belongs to
# an execution, not to a sampler, and contextvars are not meant to be created per
# instance.
_EMIT_CTX_KEY = "_dd.debugger.sampling.emit"
_FIRED_CTX_KEY = "_dd.debugger.sampling.fired"


class SampleFingerprint(t.NamedTuple):
    """Identifies a probe firing for the purpose of the per-probe cap.

    Two firings with the same fingerprint are the same probe going off in the same
    place, and only the first of them emits.
    """

    #: The span the probe fired in, or failing that the frame, which is the
    #: closest thing to a span with no trace running.
    scope: t.Optional[int]
    probe_id: str

    @classmethod
    def key(cls, probe: Probe, frame: FrameType, trace_context: t.Optional[t.Any]) -> "SampleFingerprint":
        """The fingerprint of this probe going off in this place."""
        return cls(trace_context.span_id if trace_context is not None else id(frame), probe.probe_id)

    @classmethod
    def _seen(cls) -> t.Optional[t.Set["SampleFingerprint"]]:  # noqa: UP006
        """The fingerprints witnessed so far in the current unit of execution.

        ``None`` when nothing is holding them, which is what happens for a line
        probe firing outside any probed function with no trace running. There is
        nothing to remember it with, so it is not capped.
        """
        root = tracer.current_root_span()
        if root is not None:
            seen = root._get_ctx_item(_FIRED_CTX_KEY)
            if seen is None:
                seen = set()
                root._set_ctx_item(_FIRED_CTX_KEY, seen)
            return t.cast(t.Set["SampleFingerprint"], seen)  # noqa: UP006

        return _seen_fingerprints.get()

    @classmethod
    def witnessed(cls, probe: Probe, frame: FrameType, trace_context: t.Optional[t.Any]) -> bool:
        """Whether this probe has already gone off in this place."""
        seen = cls._seen()
        return seen is not None and cls.key(probe, frame, trace_context) in seen

    @classmethod
    def witness(cls, probe: Probe, frame: FrameType, trace_context: t.Optional[t.Any]) -> None:
        """Note that this probe has gone off in this place."""
        seen = cls._seen()
        if seen is not None:
            seen.add(cls.key(probe, frame, trace_context))


_emit: ContextVar[t.Optional[bool]] = ContextVar("dd_debugger_sampling_emit", default=None)
_seen_fingerprints: ContextVar[t.Optional[t.Set[SampleFingerprint]]] = ContextVar(  # noqa: UP006
    "dd_debugger_sampling_fingerprints", default=None
)


class ScopeToken(t.NamedTuple):
    """What it takes to undo a unit of execution opened in a contextvar.

    One token per store, because a unit is a decision plus the record of what has
    already emitted under it, and both have to be restored together.
    """

    emit: Token[t.Optional[bool]]
    fingerprints: Token[t.Optional[t.Set[SampleFingerprint]]]  # noqa: UP006

    @classmethod
    def open(cls, emit: bool) -> "ScopeToken":
        """Set up a fresh unit of execution with the given decision."""
        return cls(emit=_emit.set(emit), fingerprints=_seen_fingerprints.set(set()))

    def reset(self) -> None:
        """Restore the stores this token was taken from."""
        try:
            _emit.reset(self.emit)
            _seen_fingerprints.reset(self.fingerprints)
        except ValueError:
            # The tokens were created in a different context, because the
            # invocation hopped threads or tasks between entry and exit. The state
            # is unreachable from here and dies with its context.
            pass


class DebuggerSampler(BudgetRateLimiterWithJitter):
    """Decides which probe firings emit, and accounts for the ones that do.

    A rate limiter at heart, because the ceiling it enforces *is* the sampling
    decision: a unit of execution emits if earlier ones have not already spent the
    budget. What it adds on top is the notion of that unit, so that the decision
    is taken once and shared by every probe firing within it, and a per-probe cap
    so that one probe in a loop cannot crowd out the probes around it.
    """

    def open_scope(self) -> t.Optional[ScopeToken]:
        """Start a unit of execution for the current invocation, deciding upfront.

        The decision is the whole question of whether earlier invocations have
        already spent the budget. If they have, this unit drops until it refills.

        Nesting is a no-op: an inner invocation joins the unit already in scope
        rather than competing with it. With a trace running the state lives on the
        span and dies with it, so there is nothing to close and no token.
        """
        root = tracer.current_root_span()
        if root is not None:
            if root._get_ctx_item(_EMIT_CTX_KEY) is None:
                root._set_ctx_item(_EMIT_CTX_KEY, self.has_budget())
            return None

        if _emit.get() is not None:
            return None

        return ScopeToken.open(self.has_budget())

    def close_scope(self, token: t.Optional[ScopeToken]) -> None:
        """Discard the unit of execution opened under ``token``.

        A no-op when there is no token, which is the case for a unit that lives on
        a span, or for an invocation that joined one already in scope.
        """
        if token is not None:
            token.reset()

    def _emits(self) -> bool:
        """Whether probes in the current unit of execution should emit."""
        root = tracer.current_root_span()
        if root is not None:
            decision = root._get_ctx_item(_EMIT_CTX_KEY)
            if decision is None:
                # No probed invocation has opened a unit on this trace yet, which
                # is what happens when a line probe fires outside any probed
                # function.
                decision = self.has_budget()
                root._set_ctx_item(_EMIT_CTX_KEY, decision)
            return bool(decision)

        decision = _emit.get()
        if decision is None:
            # Nothing is holding a decision, so decide for this firing alone.
            return self.has_budget()

        return decision

    def evaluate(self, probe: Probe, frame: FrameType, trace_context: t.Optional[t.Any]) -> Decision:
        """Decide whether a probe firing may proceed.

        Nothing is consumed here. Both the budget and the per-probe cap are
        charged by :meth:`account_for`, once a snapshot has actually captured
        something, so a probe whose condition does not match keeps its chance to
        emit later.
        """
        if Session.is_active_for(probe):
            return Decision.FIRE

        if not probe.is_sampled():
            if isinstance(probe, RateLimitMixin) and probe.limiter.limit() is RateLimitExceeded:
                return Decision.DROP_RATE
            return Decision.FIRE

        if not self._emits():
            return Decision.DROP_SAMPLED

        if SampleFingerprint.witnessed(probe, frame, trace_context):
            return Decision.DROP_CAPPED

        return Decision.FIRE

    def account_for(self, probe: Probe, frame: FrameType, trace_context: t.Optional[t.Any]) -> None:
        """Account for a snapshot that captured and is on its way out.

        Charged at the point of emission rather than at the gate, because what the
        budget protects is the cost of capturing values, and only a snapshot that
        got that far has paid it. The cost is already incurred by now, so it is
        spent unconditionally: a unit that overshoots puts the budget into deficit,
        and the overshoot is repaid before anything else is let through.

        A probe that is not sampled is not held to the budget, and one whose trace
        is being debugged is on the session's own accounting instead.
        """
        if Session.is_active_for(probe) or not probe.is_sampled():
            return

        SampleFingerprint.witness(probe, frame, trace_context)

        self.consume()
