import asyncio
from threading import Event
from threading import Thread
import time

import pytest

from ddtrace import tracer
from ddtrace.debugging import _sampling
from ddtrace.debugging._sampling import DebuggerSampler
from ddtrace.debugging._sampling import Decision
from ddtrace.debugging._signal.model import SignalState
from ddtrace.internal.rate_limiter import RateLimitExceeded
from tests.debugging.utils import create_log_line_probe


def budget_of(n):
    """A sampler with room for ``n`` snapshots.

    Replenishment is proportional to elapsed time, so over the microseconds a
    test takes it is immaterial.
    """
    return DebuggerSampler(limit_rate=float(n))


def spent_budget():
    """A sampler with no room left, and no replenishment to bring it back.

    A rate of zero gives a single token that never accrues again.
    """
    sampler = DebuggerSampler(limit_rate=0.0)
    sampler._limiter.consume()
    return sampler


class _Limiter:
    def __init__(self, allow):
        self._budget = 1.0 if allow else 0.0

    def has_budget(self):
        return self._budget >= 1.0

    def limit(self):
        if self._budget >= 1.0:
            self._budget -= 1.0
            return None
        return RateLimitExceeded


class _Probe:
    """Stands in for a probe: sampling only needs an ID and a rate limiter."""

    def __init__(self, probe_id="probe", snapshot=True, allow=True, tags=None):
        self.probe_id = probe_id
        self.limiter = _Limiter(allow)
        self.tags = tags or {}
        self._snapshot = snapshot

    def is_sampled(self):
        return self._snapshot


@pytest.fixture
def roomy():
    """A budget that never runs out."""
    return budget_of(float("inf"))


@pytest.fixture
def spent():
    """A budget that is already exhausted."""
    return spent_budget()


@pytest.fixture
def in_session(monkeypatch):
    """Report a session as active on the current trace for every probe."""
    monkeypatch.setattr(_sampling.Session, "is_active", staticmethod(lambda ident: True))


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def test_trace_is_admitted_at_its_first_capture(roomy):
    with tracer.trace("chain"):
        assert roomy.evaluate(_Probe("probe-a")) is Decision.FIRE
        assert roomy.limit(_Probe("probe-a")) is None
        # The decision lives on the trace, not on the sampler, so a budget that
        # is exhausted afterwards must not change it, or the probes in a chain
        # would disagree with each other.
        assert spent_budget().evaluate(_Probe("probe-b")) is Decision.FIRE


def test_trace_drops_when_budget_already_spent(spent):
    with tracer.trace("chain"):
        assert spent.evaluate(_Probe()) is Decision.DROP_SAMPLED


def test_probes_in_a_trace_share_the_decision(spent):
    with tracer.trace("chain"):
        # The first probe resolves the trace to DROP and the rest inherit it,
        # even if budget turns up in between, so the chain arrives whole or not
        # at all.
        assert spent.evaluate(_Probe("probe-a")) is Decision.DROP_SAMPLED
        spent._limiter.budget = 100.0
        assert spent.evaluate(_Probe("probe-b")) is Decision.DROP_SAMPLED
        assert spent.limit(_Probe("probe-b")) is SignalState.SKIP_RATE_GLOBAL


def test_without_a_trace_each_firing_decides_for_itself():
    # There is no unit to share a decision with, so the first firing spending
    # the budget leaves nothing for the next one.
    budget = budget_of(1)

    assert budget.evaluate(_Probe("probe-a")) is Decision.FIRE
    assert budget.limit(_Probe("probe-a")) is None
    assert budget.evaluate(_Probe("probe-b")) is Decision.DROP_SAMPLED
    assert budget.limit(_Probe("probe-b")) is SignalState.SKIP_RATE_GLOBAL


def test_snapshot_probe_ignores_its_own_rate_limit_in_a_trace(roomy):
    with tracer.trace("chain"):
        # The per-probe rate no longer drives the decision, so one exhausted
        # probe cannot starve the others sharing its trace.
        probe = _Probe(allow=False)
        assert roomy.evaluate(probe) is Decision.FIRE
        assert roomy.limit(probe) is None


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------


def test_concurrent_traces_cannot_share_the_last_token():
    # Both traces pass the gate on the same last token, since the gate only
    # peeks. Admission is atomic, so only one of them gets to capture.
    budget = budget_of(1)
    probe = _Probe("probe")
    gated, admitted = Event(), Event()
    outcomes = {}

    def first():
        with tracer.trace("first"):
            outcomes["first-gate"] = budget.evaluate(probe)
            gated.set()
            admitted.wait()
            outcomes["first"] = budget.limit(probe)

    def second():
        gated.wait()
        with tracer.trace("second"):
            outcomes["second-gate"] = budget.evaluate(probe)
            outcomes["second"] = budget.limit(probe)
            admitted.set()

    threads = [Thread(target=first), Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes["first-gate"] is outcomes["second-gate"] is Decision.FIRE
    assert outcomes["second"] is None
    assert outcomes["first"] is SignalState.SKIP_RATE_GLOBAL


def test_trace_that_never_captures_holds_nothing_back():
    # A trace whose condition never matches passes the gate but is never
    # admitted, so it must not keep a trace that does capture from the budget.
    budget = budget_of(1)

    with tracer.trace("never-matches"):
        assert budget.evaluate(_Probe("probe")) is Decision.FIRE

    with tracer.trace("matches"):
        assert budget.evaluate(_Probe("probe")) is Decision.FIRE
        assert budget.limit(_Probe("probe")) is None


def test_trace_turned_away_at_admission_stays_dropped():
    budget = budget_of(1)

    with tracer.trace("admitted"):
        assert budget.limit(_Probe("probe")) is None

    with tracer.trace("turned-away"):
        assert budget.limit(_Probe("probe")) is SignalState.SKIP_RATE_GLOBAL
        # Budget turning up later must not let the rest of the trace through.
        budget._limiter.budget = 100.0
        assert budget.evaluate(_Probe("other-probe")) is Decision.DROP_SAMPLED
        assert budget.limit(_Probe("other-probe")) is SignalState.SKIP_RATE_GLOBAL


def test_every_capture_in_an_admitted_trace_is_charged():
    budget = budget_of(2)

    with tracer.trace("chain"):
        assert budget.limit(_Probe("probe-a")) is None
        assert budget._limiter.has_budget() is True
        assert budget.limit(_Probe("probe-b")) is None
        assert budget._limiter.has_budget() is False


def test_overshooting_a_budget_leaves_a_deficit():
    # An admitted trace keeps capturing, so it can spend more than the budget
    # allowed. The overshoot has to be repaid, not forgiven.
    budget = budget_of(1)

    with tracer.trace("chain"):
        for i in range(3):
            assert budget.limit(_Probe(f"probe-{i}")) is None

    assert budget._limiter.has_budget() is False
    # One token's worth of replenishment is not enough to clear a deficit of two.
    budget._limiter.budget += 1.0
    assert budget._limiter.has_budget() is False

    with tracer.trace("next"):
        assert budget.evaluate(_Probe("probe-0")) is Decision.DROP_SAMPLED


# ---------------------------------------------------------------------------
# Participation
# ---------------------------------------------------------------------------


def test_non_snapshot_probes_do_not_participate(spent):
    # They are cheap and not held to the global budget, so an exhausted budget
    # must not suppress them.
    assert spent.evaluate(_Probe(snapshot=False)) is Decision.FIRE
    assert spent.limit(_Probe(snapshot=False)) is None


def test_non_snapshot_probes_keep_their_own_rate_limit(roomy):
    # A real log probe, since this path turns on the RateLimitMixin. A rate of
    # zero is a single-shot budget that never replenishes, so the limiter stays
    # exhausted for the rest of the test.
    probe = create_log_line_probe(
        probe_id="log-probe",
        source_file="test.py",
        line=1,
        template="",
        segments=[],
        rate=0.0,
    )
    assert probe.is_sampled() is False
    assert probe.limiter.limit() is not RateLimitExceeded  # consume the one token

    assert roomy.evaluate(probe) is Decision.DROP_RATE


def test_non_snapshot_probes_are_not_charged():
    # They are not held to the budget, so admitting one must not spend it.
    budget = budget_of(1)

    budget.limit(_Probe(snapshot=False))

    assert budget._limiter.has_budget() is True


def test_non_snapshot_probes_are_charged_only_when_they_capture(roomy):
    # Evaluating only peeks at the probe's own rate limit, so a condition that
    # does not match must not spend the one token a zero rate ever grants. Of
    # the firings that pass the peek, only one gets the token.
    probe = create_log_line_probe(
        probe_id="log-probe",
        source_file="test.py",
        line=1,
        template="",
        segments=[],
        rate=0.0,
    )

    assert roomy.evaluate(probe) is Decision.FIRE
    assert roomy.evaluate(probe) is Decision.FIRE

    assert roomy.limit(probe) is None
    assert roomy.limit(probe) is SignalState.SKIP_RATE_PROBE

    assert roomy.evaluate(probe) is Decision.DROP_RATE


# ---------------------------------------------------------------------------
# Live Debugger sessions
# ---------------------------------------------------------------------------


def test_session_probes_bypass_coordination(spent, in_session):
    # Sessions have their own per-probe budget, so coordinated sampling leaves
    # them alone rather than deciding the same thing twice.
    probe = _Probe("session-probe", tags={"session_id": "sid"})
    assert spent.evaluate(probe) is Decision.FIRE
    assert spent.limit(probe) is None


def test_session_probes_are_not_charged(in_session):
    budget = budget_of(1)

    budget.limit(_Probe("session-probe", tags={"session_id": "sid"}))

    assert budget._limiter.has_budget() is True


# ---------------------------------------------------------------------------
# The per-probe cap
# ---------------------------------------------------------------------------


def test_cap_is_charged_at_capture_not_at_the_gate(roomy):
    with tracer.trace("chain"):
        probe = _Probe("loop-probe")

        # Evaluating does not charge, so a conditional probe that does not match
        # keeps its chance to capture later in the same trace.
        for _ in range(3):
            assert roomy.evaluate(probe) is Decision.FIRE

        assert roomy.limit(probe) is None
        assert roomy.evaluate(probe) is Decision.DROP_CAPPED


def test_cap_closes_the_race_within_a_trace(roomy):
    # Recursive or overlapping calls in one trace all pass the gate before any
    # of them captures. Only the first to reach capture gets to.
    with tracer.trace("chain"):
        probe = _Probe("recursive-probe")

        assert all(roomy.evaluate(probe) is Decision.FIRE for _ in range(3))

        assert roomy.limit(probe) is None
        assert roomy.limit(probe) is SignalState.SKIP_BUDGET
        assert roomy.limit(probe) is SignalState.SKIP_BUDGET


def test_capped_capture_is_not_charged():
    budget = budget_of(2)

    with tracer.trace("chain"):
        probe = _Probe("probe")
        assert budget.limit(probe) is None
        assert budget.limit(probe) is SignalState.SKIP_BUDGET

    # Only the capture that went ahead was charged.
    assert budget._limiter.has_budget() is True


def test_cap_is_per_probe(roomy):
    with tracer.trace("chain"):
        roomy.limit(_Probe("loop-probe"))

        assert roomy.evaluate(_Probe("loop-probe")) is Decision.DROP_CAPPED
        # A sibling probe is never starved by the loop.
        assert roomy.evaluate(_Probe("entry-probe")) is Decision.FIRE


def test_cap_is_per_trace_not_per_span(roomy):
    probe = _Probe("loop-probe")

    with tracer.trace("parent"):
        with tracer.trace("child-1"):
            roomy.limit(probe)

        with tracer.trace("child-2"):
            # A different span in the same trace shares the trace's cap.
            assert roomy.evaluate(probe) is Decision.DROP_CAPPED


def test_cap_is_one_snapshot_per_trace(roomy):
    # The cap is a single shot, not a rate, so however much time passes the
    # probe does not capture again within the same trace.
    with tracer.trace("chain"):
        probe = _Probe("loop-probe")

        roomy.limit(probe)
        assert roomy.evaluate(probe) is Decision.DROP_CAPPED

        time.sleep(0.01)

        assert roomy.evaluate(probe) is Decision.DROP_CAPPED


# ---------------------------------------------------------------------------
# Trace lifetime
# ---------------------------------------------------------------------------


def test_cap_resets_between_traces(roomy):
    probe = _Probe("loop-probe")

    for _ in range(3):
        with tracer.trace("chain"):
            # Each trace is its own unit, so the probe captures every time.
            assert roomy.evaluate(probe) is Decision.FIRE
            assert roomy.limit(probe) is None
            assert roomy.evaluate(probe) is Decision.DROP_CAPPED


def test_no_trace_falls_back_to_the_probes_own_rate_limiter(roomy):
    # With no trace there is nowhere to remember what captured. Rather than
    # capturing uncapped, the probe falls back to its own rate limiter. The gate
    # only peeks at it, so two firings can pass, but only one gets the token.
    probe = _Probe("probe")

    assert roomy.evaluate(probe) is Decision.FIRE
    assert roomy.evaluate(probe) is Decision.FIRE

    assert roomy.limit(probe) is None
    assert roomy.limit(probe) is SignalState.SKIP_RATE_PROBE

    assert roomy.evaluate(probe) is Decision.DROP_CAPPED


def test_traces_do_not_leak_across_threads(roomy):
    with tracer.trace("chain"):
        probe = _Probe("probe")
        roomy.limit(probe)
        assert roomy.evaluate(probe) is Decision.DROP_CAPPED

        outcomes = []

        def other_thread():
            # Each thread has its own active trace, so it must not see the one
            # above.
            with tracer.trace("other"):
                outcomes.append(roomy.evaluate(probe))

        t = Thread(target=other_thread)
        t.start()
        t.join()

        assert outcomes == [Decision.FIRE]


async def test_traces_do_not_leak_across_tasks(roomy):
    # Sibling tasks each run their own trace, and keep it even while both are
    # open at once. The second task starts its trace before the first one
    # captures, so state shared across tasks would have the second find itself
    # capped by its sibling.
    probe = _Probe("probe")
    opened_first, opened_second = asyncio.Event(), asyncio.Event()
    captured, checked = asyncio.Event(), asyncio.Event()
    outcomes = []

    async def capture_first():
        with tracer.trace("first"):
            opened_first.set()
            await opened_second.wait()
            roomy.limit(probe)
            captured.set()
            await checked.wait()

    async def look_second():
        await opened_first.wait()
        try:
            with tracer.trace("second"):
                opened_second.set()
                await captured.wait()
                outcomes.append(roomy.evaluate(probe))
        finally:
            checked.set()

    await asyncio.gather(
        asyncio.ensure_future(capture_first()),
        asyncio.ensure_future(look_second()),
    )

    assert outcomes == [Decision.FIRE]
