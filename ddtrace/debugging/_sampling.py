"""Coordinated sampling for Live Debugger probes.

Every probe used to make its own sampling decision, so the snapshots from a chain
of related calls arrived in pieces, with no signal to the consumer that anything
was missing. Here the decision is made once per trace and inherited by every
probe that fires within it, so a chain either arrives whole or not at all.

The decision belongs to the debugger, not to the signals it emits: a signal
records what happened, it does not choose whether to happen.

Each firing goes through two checks. The gate, before the condition is
evaluated, is a cheap peek that turns away firings that clearly cannot capture.
The global limiter, once the condition has matched and right before values are
captured, is where a trace is admitted: atomically, against a global rate limit,
so that concurrent traces cannot all be admitted on the same last token. Every
capture in an admitted trace is charged, and the budget may go into deficit, so
new traces are turned away until it has been repaid.
"""

from enum import Enum
import typing as t

from ddtrace import tracer
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._session import Session
from ddtrace.debugging._signal.model import SignalState
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.rate_limiter import RateLimitExceeded


class Decision(str, Enum):
    """Outcome of the sampling gate for a single probe firing."""

    FIRE = "FIRE"
    #: The trace was not admitted, or there is no budget left to admit it.
    DROP_SAMPLED = "DROP_SAMPLED"
    #: The probe already captured in this trace, or is over its own rate
    #: outside of one.
    DROP_CAPPED = "DROP_CAPPED"
    #: The probe is not sampled and is over its own rate.
    DROP_RATE = "DROP_RATE"


# Both the decision and the per-probe cap live on the local root span, so one
# decision covers the whole trace and the state dies with it. A trace runs on the
# thread it started on, so this state is never contended. With no trace running
# there is no unit to coordinate, so each firing is admitted on its own and is
# capped by the probe's own rate limiter, as probes always were.
_EMIT_CTX_KEY = "_dd.debugger.sampling.emit"
_FIRED_CTX_KEY = "_dd.debugger.sampling.fired"


def _fired(root: t.Any) -> set[str]:
    """The IDs of the probes that have already captured in the trace."""
    fired = root._get_ctx_item(_FIRED_CTX_KEY)
    if fired is None:
        fired = set()
        root._set_ctx_item(_FIRED_CTX_KEY, fired)
    return t.cast(set[str], fired)


def _over_rate(probe: Probe, peek: bool) -> bool:
    """Whether the probe is over its own rate limit, charging it unless peeking."""
    limiter = getattr(probe, "limiter", None)
    if limiter is None:
        return False
    if peek:
        return not limiter.has_budget()
    return limiter.limit() is RateLimitExceeded


class DebuggerSampler:
    """Decides which probe firings capture, and accounts for the ones that do.

    The decision is taken once per trace, by admitting it against a global rate
    limit at its first capture, and shared by every probe firing within it. On
    top of that, each probe captures at most once per trace, so that one probe in
    a loop cannot crowd out the probes around it.
    """

    def __init__(self, limit_rate: float, on_exceed: t.Optional[t.Callable[[], t.Any]] = None) -> None:
        self._limiter = RateLimiter(
            limit_rate=limit_rate,
            on_exceed=on_exceed,
            call_once=True,
            raise_on_exceed=False,
        )

    def evaluate(self, probe: Probe) -> Decision:
        """Decide whether a probe firing may proceed to its condition.

        Only peeks, so a probe whose condition does not match keeps its chance to
        capture later, and holds nothing back from other traces meanwhile. The
        one thing recorded here is a drop for a trace that finds the budget
        already spent, so that the trace is not admitted halfway through once the
        budget is repaid.
        """
        if Session.is_active_for(probe):
            return Decision.FIRE

        if not probe.is_sampled():
            return Decision.DROP_RATE if _over_rate(probe, peek=True) else Decision.FIRE

        root = tracer.current_root_span()
        if root is None:
            if not self._limiter.has_budget():
                return Decision.DROP_SAMPLED
            return Decision.DROP_CAPPED if _over_rate(probe, peek=True) else Decision.FIRE

        decision = root._get_ctx_item(_EMIT_CTX_KEY)
        if decision is None and not self._limiter.has_budget():
            decision = False
            root._set_ctx_item(_EMIT_CTX_KEY, decision)
        if decision is False:
            return Decision.DROP_SAMPLED

        if probe.probe_id in _fired(root):
            return Decision.DROP_CAPPED

        return Decision.FIRE

    def limit(self, probe: Probe) -> t.Optional[SignalState]:
        """Admit a probe firing whose condition has matched, right before capture.

        This is the global limiter the signals consult. The first capture in a
        trace admits the trace, atomically, against the global rate limit. Every
        later capture in an admitted trace is charged unconditionally, so a trace
        that overshoots puts the budget into deficit, and new traces are turned
        away until it is repaid. Returns the state to skip the signal with, or
        None if it may capture.

        A probe that is not sampled is held to its own rate limit instead of the
        global one. One whose trace is being debugged is on the session's own
        accounting instead.
        """
        if Session.is_active_for(probe):
            return None

        if not probe.is_sampled():
            return SignalState.SKIP_RATE_PROBE if _over_rate(probe, peek=False) else None

        root = tracer.current_root_span()
        if root is None:
            # Charge the probe's own rate first: it is the cheaper token to waste
            # should the global limit then turn the firing away.
            if _over_rate(probe, peek=False):
                return SignalState.SKIP_RATE_PROBE
            if self._limiter.limit() is RateLimitExceeded:
                return SignalState.SKIP_RATE_GLOBAL
            return None

        fired = _fired(root)
        if probe.probe_id in fired:
            return SignalState.SKIP_BUDGET

        decision = root._get_ctx_item(_EMIT_CTX_KEY)
        if decision is None:
            decision = self._limiter.limit() is not RateLimitExceeded
            root._set_ctx_item(_EMIT_CTX_KEY, decision)
        elif decision:
            self._limiter.consume()

        if not decision:
            return SignalState.SKIP_RATE_GLOBAL

        fired.add(probe.probe_id)

        return None
