import asyncio
import sys
from threading import Thread

import pytest

from ddtrace.debugging import _sampling
from ddtrace.debugging._sampling import DebuggerSampler
from ddtrace.debugging._sampling import Decision
from ddtrace.internal.rate_limiter import RateLimitExceeded
from tests.debugging.utils import create_log_line_probe


def budget_of(n):
    """A sampler with room for ``n`` snapshots.

    Replenishment is proportional to elapsed time, so over the microseconds a
    test takes it is immaterial.
    """
    return DebuggerSampler(limit_rate=float(n), raise_on_exceed=False)


def spent_budget():
    """A sampler with no room left, and no replenishment to bring it back.

    A rate of zero gives a single token that never accrues again.
    """
    limiter = DebuggerSampler(limit_rate=0.0, raise_on_exceed=False)
    limiter.consume()
    return limiter


class _Limiter:
    def __init__(self, allow):
        self._allow = allow

    def limit(self):
        return None if self._allow else RateLimitExceeded


class _Probe:
    """Stands in for a probe: sampling only needs an ID and a rate limiter."""

    def __init__(self, probe_id="probe", snapshot=True, allow=True, tags=None):
        self.probe_id = probe_id
        self.limiter = _Limiter(allow)
        self.tags = tags or {}
        self._snapshot = snapshot

    def is_sampled(self):
        return self._snapshot


def frame():
    """A frame object of its own, kept alive by the caller's reference."""
    return sys._getframe()


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


def test_scope_decides_upfront(roomy):
    token = roomy.open_scope()
    try:
        # Exhausting the budget afterwards must not change a decision already
        # made, or the probes in a chain would disagree with each other.
        assert spent_budget().evaluate(_Probe(), frame(), None) is Decision.FIRE
    finally:
        roomy.close_scope(token)


def test_scope_drops_when_budget_already_spent(spent):
    token = spent.open_scope()
    try:
        assert spent.evaluate(_Probe(), frame(), None) is Decision.DROP_SAMPLED
    finally:
        spent.close_scope(token)


def test_probes_in_a_scope_share_the_decision(spent):
    token = spent.open_scope()
    try:
        # The first probe resolves the unit to DROP and the rest inherit it, so
        # the chain arrives whole or not at all.
        f = frame()
        assert spent.evaluate(_Probe("probe-a"), f, None) is Decision.DROP_SAMPLED
        assert spent.evaluate(_Probe("probe-b"), f, None) is Decision.DROP_SAMPLED
    finally:
        spent.close_scope(token)


def test_nested_scopes_join_the_outer_one(roomy):
    token = roomy.open_scope()
    try:
        # An inner probed invocation must not start a competing unit, or a
        # callee's probe could disagree with its caller's.
        assert roomy.open_scope() is None
    finally:
        roomy.close_scope(token)


def test_snapshot_probe_ignores_its_own_rate_limit(roomy):
    token = roomy.open_scope()
    try:
        # The per-probe rate no longer drives the decision, so one exhausted
        # probe cannot starve the others sharing its unit.
        assert roomy.evaluate(_Probe(allow=False), frame(), None) is Decision.FIRE
    finally:
        roomy.close_scope(token)


# ---------------------------------------------------------------------------
# Participation
# ---------------------------------------------------------------------------


def test_non_snapshot_probes_do_not_participate(spent):
    # They are cheap and not held to the global budget, so an exhausted budget
    # must not suppress them.
    assert spent.evaluate(_Probe(snapshot=False), frame(), None) is Decision.FIRE


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

    assert roomy.evaluate(probe, frame(), None) is Decision.DROP_RATE


def test_non_snapshot_probes_are_not_charged():
    # They are not held to the budget, so accounting for one must not spend it.
    budget = budget_of(1)

    budget.account_for(_Probe(snapshot=False), frame(), None)

    assert budget.has_budget() is True


# ---------------------------------------------------------------------------
# Live Debugger sessions
# ---------------------------------------------------------------------------


def test_session_probes_bypass_coordination(spent, in_session):
    # Sessions have their own per-probe budget, so coordinated sampling leaves
    # them alone rather than deciding the same thing twice.
    probe = _Probe("session-probe", tags={"session_id": "sid"})
    assert spent.evaluate(probe, frame(), None) is Decision.FIRE


def test_session_probes_are_not_charged(in_session):
    budget = budget_of(1)

    budget.account_for(_Probe("session-probe", tags={"session_id": "sid"}), frame(), None)

    assert budget.has_budget() is True


# ---------------------------------------------------------------------------
# The per-probe cap
# ---------------------------------------------------------------------------


def test_cap_is_recorded_not_reserved(roomy):
    token = roomy.open_scope()
    try:
        probe = _Probe("loop-probe")
        f = frame()

        # Evaluating does not claim, so a conditional probe that does not match
        # keeps its chance to emit later in the same place.
        for _ in range(3):
            assert roomy.evaluate(probe, f, None) is Decision.FIRE

        roomy.account_for(probe, f, None)
        assert roomy.evaluate(probe, f, None) is Decision.DROP_CAPPED
    finally:
        roomy.close_scope(token)


def test_cap_is_per_probe(roomy):
    token = roomy.open_scope()
    try:
        f = frame()
        roomy.account_for(_Probe("loop-probe"), f, None)

        assert roomy.evaluate(_Probe("loop-probe"), f, None) is Decision.DROP_CAPPED
        # A sibling probe is never starved by the loop.
        assert roomy.evaluate(_Probe("entry-probe"), f, None) is Decision.FIRE
    finally:
        roomy.close_scope(token)


def test_cap_is_per_frame_without_a_trace(roomy):
    token = roomy.open_scope()
    try:
        probe = _Probe("loop-probe")
        # Both held alive, so their ids cannot coincide.
        first, second = frame(), frame()

        roomy.account_for(probe, first, None)

        assert roomy.evaluate(probe, first, None) is Decision.DROP_CAPPED
        # A different frame is a different invocation, so the probe gets a fresh
        # slot.
        assert roomy.evaluate(probe, second, None) is Decision.FIRE
    finally:
        roomy.close_scope(token)


def test_recording_charges_the_budget(roomy):
    token = roomy.open_scope()
    try:
        budget = budget_of(2)
        f = frame()

        budget.account_for(_Probe("probe-a"), f, None)
        assert budget.has_budget() is True
        budget.account_for(_Probe("probe-b"), f, None)
        assert budget.has_budget() is False
    finally:
        roomy.close_scope(token)


# ---------------------------------------------------------------------------
# Scope lifetime
# ---------------------------------------------------------------------------


def test_cap_resets_between_invocations(roomy):
    probe = _Probe("loop-probe")
    f = frame()

    for _ in range(3):
        token = roomy.open_scope()
        try:
            # Each invocation is its own unit, so the probe fires every time even
            # though the frame is the same.
            assert roomy.evaluate(probe, f, None) is Decision.FIRE
            roomy.account_for(probe, f, None)
            assert roomy.evaluate(probe, f, None) is Decision.DROP_CAPPED
        finally:
            roomy.close_scope(token)


def test_no_scope_means_no_shared_state(roomy):
    # A line probe outside any probed function has nothing to close a scope, so
    # it must not strand state for the life of the thread.
    probe = _Probe("probe")
    f = frame()

    roomy.account_for(probe, f, None)
    assert roomy.evaluate(probe, f, None) is Decision.FIRE


def test_scopes_do_not_leak_across_threads(roomy):
    token = roomy.open_scope()
    try:
        probe = _Probe("probe")
        f = frame()
        roomy.account_for(probe, f, None)
        assert roomy.evaluate(probe, f, None) is Decision.DROP_CAPPED

        outcomes = []

        def other_thread():
            # Each thread has its own context, so it must not see the scope above.
            outcomes.append(roomy.evaluate(probe, f, None))

        t = Thread(target=other_thread)
        t.start()
        t.join()

        assert outcomes == [Decision.FIRE]
    finally:
        roomy.close_scope(token)


def test_overshooting_a_budget_leaves_a_deficit(roomy):
    # A unit that has resolved to EMIT keeps emitting, so it can spend more than
    # the budget allowed. The overshoot has to be repaid, not forgiven.
    budget = budget_of(1)
    f = frame()

    for i in range(3):
        budget.account_for(_Probe(f"probe-{i}"), f, None)

    assert budget.has_budget() is False
    # One token's worth of replenishment is not enough to clear a deficit of two.
    budget.budget += 1.0
    assert budget.has_budget() is False


async def test_scopes_do_not_leak_across_tasks(roomy):
    # Sibling tasks each get their own unit, and keep it even while both are open
    # at once. The ordering matters: the second task opens its scope *before* the
    # first one witnesses the probe, so state that is merely thread-local rather
    # than context-local would have the first task writing into the second task's
    # unit, and the second would find itself capped by its sibling.
    probe = _Probe("probe")
    f = frame()
    opened_first, opened_second = asyncio.Event(), asyncio.Event()
    witnessed, checked = asyncio.Event(), asyncio.Event()
    outcomes = []

    async def witness_first():
        token = roomy.open_scope()
        try:
            opened_first.set()
            await opened_second.wait()
            roomy.account_for(probe, f, None)
            witnessed.set()
            await checked.wait()
        finally:
            roomy.close_scope(token)

    async def look_second():
        await opened_first.wait()
        token = roomy.open_scope()
        try:
            opened_second.set()
            await witnessed.wait()
            outcomes.append(roomy.evaluate(probe, f, None))
        finally:
            roomy.close_scope(token)
            checked.set()

    await asyncio.gather(witness_first(), look_second())

    assert outcomes == [Decision.FIRE]
