"""Coordinated sampling for Live Debugger probes.

Probes sampled independently, so chains of related snapshots arrived in pieces.
Here the decision is made once per sampling context (currently the trace) and
shared by every probe in it, so a chain arrives whole or not at all. Each probe
captures at most once per context.

Signals check twice: a cheap gate before the condition, and atomic admission
right before capture. The first capture decides the context: it is admitted only
if the capturing probe is within its own rate and the global rate limit has
room, so that a probe firing in many concurrent contexts cannot take the whole
global budget for itself. Otherwise it is rejected, so that no later probe can
admit it with a hole where the first capture should have been. Every later
capture is charged, possibly into deficit, so new contexts are turned away until
it is repaid. A rejected context stays rejected.

Outside of any context, firings are admitted individually and also held to the
probe's own rate. Unsampled probes (no snapshot, exception replay, code origin)
only honour their own rate, and active session probes their session budget.
"""

import abc
from enum import Enum
import typing as t

import ddtrace
from ddtrace.debugging._config import di_config
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._session import Session
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.rate_limiter import RateLimitExceeded
from ddtrace.internal.rate_limiter import limit_all


class Decision(str, Enum):
    """Outcome of sampling a single probe firing."""

    FIRE = "FIRE"
    # There is no global budget left to admit the firing or its sampling
    # context, or the sampler is stopped.
    DROP_SAMPLED = "DROP_SAMPLED"
    # The sampling context was rejected by an earlier firing in it, which
    # reported why.
    DROP_REJECTED = "DROP_REJECTED"
    # The probe already captured in this sampling context.
    DROP_CAPPED = "DROP_CAPPED"
    # The probe is over its own rate: it is not sampled, it fired outside of
    # any sampling context, or it was the first to capture in one after
    # spending its rate on others, and so the context is rejected.
    DROP_RATE = "DROP_RATE"


log = get_logger(__name__)


# Makes admission within a context atomic. A context is usually only active on
# the thread that started it, but copying the execution context into another
# thread (e.g. asyncio.to_thread) shares it, so the decision and the per-probe
# cap need guarding. Taken on application threads, at the gate to reject a
# context and at admission, so it is fork-safe, and only ever held briefly.
#
# It is not re-entrant, so a probe firing on a thread that already holds it
# deadlocks. Nothing called while it is held may therefore reach application
# code, e.g. logging, whose handlers may be the application's. The interpreter
# can still run application code under it, e.g. a GC finalizer or a signal
# handler, so a probe there would deadlock. That residual hazard is rare and
# needs a re-entrancy guard to close.
_context_lock = forksafe.Lock()


class SamplingContext(abc.ABC):
    """A unit of execution that probes coordinate their sampling within.

    Holds whether the context was admitted, and which probes captured in it. The
    decision is taken at most once and never changed. Subclasses only provide
    where that state is stored, which is what ties it to the lifetime of the
    unit of execution.
    """

    __slots__ = ()

    @abc.abstractmethod
    def _get(self, name: str) -> t.Any: ...

    @abc.abstractmethod
    def _set(self, name: str, value: t.Any) -> None: ...

    @property
    def decided(self) -> bool:
        return self._get("accepted") is not None

    @property
    def accepted(self) -> bool:
        return self._get("accepted") is True

    @property
    def rejected(self) -> bool:
        return self._get("accepted") is False

    def accept(self) -> None:
        self._set("accepted", True)

    def reject(self) -> None:
        self._set("accepted", False)

    def captured(self, probe: Probe) -> bool:
        captured = self._get("captured")
        return captured is not None and probe.probe_id in captured

    def capture(self, probe: Probe) -> None:
        captured = self._get("captured")
        if captured is None:
            captured = set()
            self._set("captured", captured)
        captured.add(probe.probe_id)


class TraceContext(SamplingContext):
    """The trace as the sampling context.

    The state lives on the local root span, so one decision covers the whole
    trace and the state dies with it.
    """

    __slots__ = ("_root",)

    _KEYS = {name: f"_dd.debugger.sampling.{name}" for name in ("accepted", "captured")}

    def __init__(self, root: t.Any) -> None:
        self._root = root

    def _get(self, name: str) -> t.Any:
        return self._root._get_ctx_item(self._KEYS[name])

    def _set(self, name: str, value: t.Any) -> None:
        self._root._set_ctx_item(self._KEYS[name], value)


def _limiters(probe: Probe, limiter: RateLimiter) -> tuple[RateLimiter, ...]:
    """The limiters a sampled probe firing is admitted against, in order.

    The probe's own rate comes first, so that a firing turned away by it never
    spends a token of the global budget.
    """
    own = getattr(probe, "limiter", None)
    return (own, limiter) if own is not None else (limiter,)


def _over_rate(probe: Probe, peek: bool) -> bool:
    """Whether the probe is over its own rate limit, charging it unless peeking."""
    limiter = getattr(probe, "limiter", None)
    if limiter is None:
        return False
    if peek:
        return not limiter.has_budget()
    return limiter.limit() is RateLimitExceeded


class DebuggerSampler:
    """Global coordinated sampling state.

    Started and stopped with the debugger. While stopped, no probe that takes
    part in coordinated sampling may fire. Probes that do not take part are only
    ever held to their own rate limit, stopped or not.

    The decision is taken once per sampling context, at its first capture, and
    shared by every probe firing within it. The context is admitted against the
    capturing probe's own rate and the global rate limit, so that the contexts
    sharing the global budget are not all admitted by the same probe, and is
    rejected if either refuses. On top of that, each probe captures at most
    once per context, so that one probe in a loop cannot crowd out the probes
    around it.
    """

    _tracer: t.Optional[t.Any] = None
    _limiter: t.Optional[RateLimiter] = None
    _log_limiter = RateLimiter(limit_rate=1.0, raise_on_exceed=False)

    @classmethod
    def start(cls) -> None:
        """Start sampling against the configured global rate limit.

        Called when the debugger is enabled, rather than at import, so that the
        rate limit reads the current config.
        """
        cls._tracer = ddtrace.tracer
        # No on_exceed callback: admission goes through limit_all, which never
        # calls it. Exceeding either limit is logged by _reported instead, once
        # the context lock is released.
        cls._limiter = RateLimiter(
            limit_rate=di_config.global_rate_limit,  # TODO: Make it configurable. Note that this is per-process!
            raise_on_exceed=False,
        )

    @classmethod
    def stop(cls) -> None:
        cls._tracer = cls._limiter = None

    @classmethod
    def _limit(cls, probe: Probe, limiter: RateLimiter, peek: bool) -> Decision:
        """Hold a sampled probe firing to its own rate and the global rate limit.

        Both are charged, unless peeking, or neither is.
        """
        refused = limit_all(*_limiters(probe, limiter), peek=peek)
        if refused is None:
            return Decision.FIRE
        return Decision.DROP_SAMPLED if refused is limiter else Decision.DROP_RATE

    @classmethod
    def _reported(cls, probe: Probe, decision: Decision) -> Decision:
        """Report a sampled probe firing freshly turned away by a rate limit.

        Must be called outside the context lock, as logging may call into
        application code.
        """
        if decision is Decision.DROP_SAMPLED:
            cls._log_limiter.limit(log.debug, "Global rate limit exceeded")
        elif decision is Decision.DROP_RATE:
            cls._log_limiter.limit(log.debug, "Rate limit exceeded for probe %s", probe.probe_id)
        return decision

    @classmethod
    def _context(cls) -> t.Optional[SamplingContext]:
        """The sampling context the current firing belongs to, if any.

        Currently the trace. Another kind of context (e.g. the runtime execution
        context, for firings with no trace) only needs to be derived here.
        """
        root = cls._tracer.current_root_span() if cls._tracer is not None else None
        return TraceContext(root) if root is not None else None

    @classmethod
    def evaluate(cls, probe: Probe) -> Decision:
        """Decide whether a probe firing may proceed to its condition.

        Only peeks, so a probe whose condition does not match keeps its chance to
        capture later, and holds nothing back from other contexts meanwhile. The
        one thing recorded here is the rejection of a context that finds the
        budget already spent, so that it is not admitted halfway through once
        the budget is repaid.

        A probe over its own rate is not turned away from an undecided context
        here: that would reject the context, or leave it to another probe to
        admit without this one, before knowing whether its condition matches.
        Admission decides once it does.
        """
        limiter = cls._limiter
        sampled = probe.is_sampled()
        if limiter is None and sampled:
            return Decision.DROP_SAMPLED

        if Session.is_active_for(probe):
            return Decision.FIRE

        if not sampled:
            return Decision.DROP_RATE if _over_rate(probe, peek=True) else Decision.FIRE

        assert limiter is not None  # nosec: sampled probes were turned away above

        context = cls._context()
        if context is None:
            return cls._limit(probe, limiter, peek=True)

        # Only an undecided context with no budget is written to here, so the
        # common path takes no lock. A decision is never changed once taken, so
        # the reads outside the lock are safe.
        if not context.decided and not limiter.has_budget():
            with _context_lock:
                # Check again: another thread may have admitted the context, or
                # the budget may have been replenished, meanwhile.
                rejecting = not context.decided and not limiter.has_budget()
                if rejecting:
                    context.reject()
            if rejecting:
                return cls._reported(probe, Decision.DROP_SAMPLED)
        if context.rejected:
            return Decision.DROP_REJECTED

        return Decision.DROP_CAPPED if context.captured(probe) else Decision.FIRE

    @classmethod
    def admit(cls, probe: Probe) -> Decision:
        """Admit a probe firing whose condition has matched, right before capture.

        The first capture in a sampling context decides the context, atomically:
        it is admitted if the probe is within its own rate and the global rate
        limit has room, and rejected otherwise. The firing that rejects it is
        told which limit refused, and every later one that the context was
        rejected, so that each drop is reported once against its cause. Every
        later capture in an admitted
        context is charged unconditionally, so a context that overshoots puts
        the budget into deficit, and new contexts are turned away until it is
        repaid. A probe that already captured in the context is turned away.

        A probe that is not sampled is held to its own rate limit instead of the
        global one. One whose trace is being debugged is on the session's own
        accounting instead.
        """
        limiter = cls._limiter
        sampled = probe.is_sampled()
        if limiter is None and sampled:
            return Decision.DROP_SAMPLED

        if Session.is_active_for(probe):
            return Decision.FIRE

        if not sampled:
            return Decision.DROP_RATE if _over_rate(probe, peek=False) else Decision.FIRE

        assert limiter is not None  # nosec: sampled probes were turned away above

        context = cls._context()
        if context is None:
            return cls._reported(probe, cls._limit(probe, limiter, peek=False))

        with _context_lock:
            if context.captured(probe):
                return Decision.DROP_CAPPED

            if context.rejected:
                return Decision.DROP_REJECTED

            if context.accepted:
                limiter.consume()
                decision = Decision.FIRE
            else:
                decision = cls._limit(probe, limiter, peek=False)
                if decision is Decision.FIRE:
                    context.accept()
                else:
                    context.reject()

            if decision is Decision.FIRE:
                context.capture(probe)

        return cls._reported(probe, decision)
