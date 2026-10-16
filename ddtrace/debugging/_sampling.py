"""Coordinated sampling for Live Debugger probes.

Every probe used to make its own sampling decision, so the snapshots from a chain
of related calls arrived in pieces, with no signal to the consumer that anything
was missing. Here the decision is made once per trace and inherited by every
probe that fires within it, so a chain either arrives whole or not at all.

The decision belongs to the debugger, not to the signals it emits: a signal
records what happened, it does not choose whether to happen.
"""

from enum import Enum
import typing as t

from ddtrace import tracer
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.model import RateLimitMixin
from ddtrace.debugging._session import Session
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter


class Decision(str, Enum):
    """Outcome of the sampling gate for a single probe firing."""

    FIRE = "FIRE"
    #: The budget was spent when this trace made its decision.
    DROP_SAMPLED = "DROP_SAMPLED"
    #: The trace is emitting, but this probe already emitted in it.
    DROP_CAPPED = "DROP_CAPPED"
    #: The probe is not sampled and is over its own rate.
    DROP_RATE = "DROP_RATE"


# Both the decision and the per-probe cap live on the local root span, so one
# decision covers the whole trace and the state dies with it. With no trace
# running there is no unit to coordinate, so each firing decides for itself
# against the global budget and is capped by the probe's own rate limiter, as
# probes always were.
_EMIT_CTX_KEY = "_dd.debugger.sampling.emit"
_FIRED_CTX_KEY = "_dd.debugger.sampling.fired"


def _fired() -> t.Optional[set[str]]:
    """The IDs of the probes that have already emitted in the current trace.

    None when no trace is running, in which case there is nowhere to remember
    them and callers fall back to the probe's own rate limiter instead.
    """
    root = tracer.current_root_span()
    if root is None:
        return None

    fired = root._get_ctx_item(_FIRED_CTX_KEY)
    if fired is None:
        fired = set()
        root._set_ctx_item(_FIRED_CTX_KEY, fired)
    return t.cast(set[str], fired)


def _capped(probe: Probe) -> bool:
    """Whether this probe has already had its one snapshot in the current trace.

    With no trace, the probe's own rate limiter stands in, so a probe firing
    outside any trace still gets rate-limited rather than firing unconditionally.
    """
    fired = _fired()
    if fired is None:
        probe_limiter = getattr(probe, "limiter", None)
        return probe_limiter is not None and not probe_limiter.has_budget()

    return probe.probe_id in fired


def _witness(probe: Probe) -> None:
    """Charge the cap for a probe that just emitted."""
    fired = _fired()
    if fired is None:
        probe_limiter = getattr(probe, "limiter", None)
        if probe_limiter is not None:
            probe_limiter.limit()
        return

    fired.add(probe.probe_id)


class DebuggerSampler(BudgetRateLimiterWithJitter):
    """Decides which probe firings emit, and accounts for the ones that do.

    A rate limiter at heart, because the ceiling it enforces *is* the sampling
    decision: a trace emits if earlier ones have not already spent the budget.
    What it adds on top is that the decision is taken once per trace and shared
    by every probe firing within it, and a per-probe cap of one snapshot per
    trace, so that one probe in a loop cannot crowd out the probes around it.
    """

    def _emits(self) -> bool:
        """Whether probes in the current trace should emit.

        The first probe to fire in a trace makes the decision for it. With no
        trace running, there is nothing to share a decision with, so the firing
        decides for itself.
        """
        root = tracer.current_root_span()
        if root is None:
            return self.has_budget()

        decision = root._get_ctx_item(_EMIT_CTX_KEY)
        if decision is None:
            decision = self.has_budget()
            root._set_ctx_item(_EMIT_CTX_KEY, decision)
        return bool(decision)

    def evaluate(self, probe: Probe) -> Decision:
        """Decide whether a probe firing may proceed.

        Nothing is consumed here. Both the budget and the per-probe cap are
        charged by account_for, once a snapshot has actually captured something,
        so a probe whose condition does not match keeps its chance to emit later.
        """
        if Session.is_active_for(probe):
            return Decision.FIRE

        if not probe.is_sampled():
            if isinstance(probe, RateLimitMixin) and not probe.limiter.reserve():
                return Decision.DROP_RATE
            return Decision.FIRE

        if not self._emits():
            return Decision.DROP_SAMPLED

        if _capped(probe):
            return Decision.DROP_CAPPED

        return Decision.FIRE

    def account_for(self, probe: Probe) -> None:
        """Account for a snapshot that captured and is on its way out.

        Charged at the point of emission rather than at the gate, because what the
        budget protects is the cost of capturing values, and only a snapshot that
        got that far has paid it. The cost is already incurred by now, so it is
        spent unconditionally: a trace that overshoots puts the budget into
        deficit, and the overshoot is repaid before anything else is let through.

        A probe that is not sampled is held to its own rate limit instead of the
        budget, charged here rather than at the gate for the same reason: only a
        snapshot that actually captured has paid the cost the limit protects.
        One whose trace is being debugged is on the session's own accounting
        instead.
        """
        if Session.is_active_for(probe):
            return

        if not probe.is_sampled():
            if isinstance(probe, RateLimitMixin):
                probe.limiter.spend()
            return

        _witness(probe)

        self.consume()
