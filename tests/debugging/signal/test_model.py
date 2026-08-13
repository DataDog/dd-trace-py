import sys
import threading
from time import monotonic
from unittest import mock

from ddtrace.debugging._expressions import DDExpression
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import DEFAULT_SNAPSHOT_PROBE_RATE
from ddtrace.debugging._signal.model import Signal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter
from tests.debugging.utils import create_log_function_probe
from tests.debugging.utils import create_snapshot_line_probe


class MockSignal(Signal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = {}

    def enter(self, scope):
        self.scope = scope

    def exit(self, retval, exc_info, duration, scope):
        self.scope = scope

    def line(self, scope):
        self.scope = scope


def _make_signal(probe):
    return MockSignal(probe=probe, frame=sys._getframe(), thread=threading.current_thread())


def test_enriched_args_locals_globals():
    duration = 123456
    signal = MockSignal(
        probe=create_log_function_probe(
            probe_id="test_duration_millis",
            module="foo",
            func_qname="bar",
            template="",
            segments=[],
        ),
        frame=sys._getframe(),
        thread=threading.current_thread(),
    )
    signal.do_exit(None, (None, None, None), duration)
    exit_scope = signal.scope

    # Check for globals
    assert "__file__" in exit_scope

    # Check for locals
    assert "duration" in exit_scope

    # Check for the correct duration units
    assert exit_scope["@duration"] == duration / 1e6


# ---------------------------------------------------------------------------
# _eval_duration_ms
# ---------------------------------------------------------------------------


def test_eval_condition_sets_eval_duration_ms():
    """_eval_duration_ms is populated after a successful condition evaluation."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        condition=DDExpression(dsl="True", callable=dd_compile(True)),
    )
    signal = _make_signal(probe)
    result = signal._eval_condition({})
    assert result is True
    assert signal._eval_duration_ms is not None
    assert signal._eval_duration_ms >= 0.0


def test_eval_condition_sets_eval_duration_ms_on_error():
    """_eval_duration_ms is still set when the condition raises."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        condition=DDExpression(dsl="missing", callable=dd_compile({"ref": "missing"})),
    )
    signal = _make_signal(probe)
    result = signal._eval_condition({})
    assert result is False
    assert signal._eval_duration_ms is not None
    assert signal._eval_duration_ms >= 0.0


# ---------------------------------------------------------------------------
# Evaluation timeout recorded as error
# ---------------------------------------------------------------------------


def test_eval_condition_timeout_records_error():
    """When evaluation exceeds the budget, an EvaluationError is appended."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        condition=DDExpression(dsl="True", callable=dd_compile(True)),
    )
    signal = _make_signal(probe)

    with mock.patch("ddtrace.debugging._signal.model.di_config") as cfg:
        cfg.evaluation_timeout_ms = -1  # any positive elapsed time exceeds budget
        result = signal._eval_condition({})

    # Condition would have returned True but the timeout causes the signal to be dropped
    assert result is False
    assert signal.state in {SignalState.COND_ERROR, SignalState.SKIP_COND_ERROR}
    timeout_errors = [e for e in signal.errors if "exceeded budget" in e.message]
    assert timeout_errors, "Expected a timeout EvaluationError"


# ---------------------------------------------------------------------------
# Probe-entry skip when throttled
# ---------------------------------------------------------------------------


def test_probe_entry_skip_when_error_throttled():
    """When _error_throttled_until is in the future, evaluation is skipped entirely."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        condition=DDExpression(dsl="x", callable=dd_compile({"ref": "x"})),
    )
    probe._error_throttled_until = monotonic() + 3600.0  # throttled for an hour

    eval_called = []
    original = probe.condition.callable

    def tracking_callable(scope):
        eval_called.append(True)
        return original(scope)

    signal = _make_signal(probe)
    with mock.patch.object(probe.condition, "callable", tracking_callable):
        result = signal._eval_condition({"x": True})

    assert result is False
    assert signal.state is SignalState.SKIP_COND_ERROR
    assert signal._eval_duration_ms is None, "_eval_duration_ms should not be set on probe-entry skip"
    assert not eval_called, "condition.callable should not be invoked when throttled"


def test_probe_entry_skip_not_triggered_when_throttle_expired():
    """When _error_throttled_until is in the past, evaluation proceeds normally."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        condition=DDExpression(dsl="True", callable=dd_compile(True)),
    )
    probe._error_throttled_until = monotonic() - 1.0  # expired

    signal = _make_signal(probe)
    result = signal._eval_condition({})

    assert result is True
    assert signal.state is SignalState.NONE


def test_repeated_eval_errors_set_throttle():
    """Once the error rate limiter fires, _error_throttled_until is set on the probe."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        condition=DDExpression(dsl="missing", callable=dd_compile({"ref": "missing"})),
    )

    # First error: within budget → COND_ERROR, no throttle
    s1 = _make_signal(probe)
    s1._eval_condition({})
    assert s1.state is SignalState.COND_ERROR
    assert probe._error_throttled_until == 0.0

    # Second error: budget exhausted → SKIP_COND_ERROR + throttle set
    s2 = _make_signal(probe)
    s2._eval_condition({})
    assert s2.state is SignalState.SKIP_COND_ERROR
    assert probe._error_throttled_until > monotonic()


# ---------------------------------------------------------------------------
# SKIP_RATE_GLOBAL vs SKIP_RATE_PROBE
# ---------------------------------------------------------------------------


def _exhausted_limiter():
    """Return a BudgetRateLimiterWithJitter whose budget is already consumed."""
    limiter = BudgetRateLimiterWithJitter(limit_rate=1.0, raise_on_exceed=False)
    limiter.limit()  # consume the single token
    return limiter


def test_do_line_global_limiter_sets_skip_rate_global():
    """When the global rate limiter is exhausted, do_line sets SKIP_RATE_GLOBAL."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
    )
    global_limiter = _exhausted_limiter()
    signal = _make_signal(probe)
    signal.do_line(global_limiter)
    assert signal.state is SignalState.SKIP_RATE_GLOBAL


def test_rate_limit_exceeded_sets_skip_rate_probe():
    """When the per-probe rate limiter is exhausted, _rate_limit_exceeded sets SKIP_RATE_PROBE."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        rate=DEFAULT_SNAPSHOT_PROBE_RATE,
    )
    probe.limiter.limit()  # consume the single token

    signal = _make_signal(probe)
    exceeded = signal._rate_limit_exceeded()
    assert exceeded is True
    assert signal.state is SignalState.SKIP_RATE_PROBE


def test_do_line_probe_limiter_sets_skip_rate_probe():
    """When only the probe limiter is exhausted (global OK), do_line sets SKIP_RATE_PROBE."""
    probe = create_snapshot_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        rate=DEFAULT_SNAPSHOT_PROBE_RATE,
    )
    probe.limiter.limit()  # exhaust probe budget

    # Fresh global limiter (not exhausted)
    global_limiter = BudgetRateLimiterWithJitter(limit_rate=1000.0, raise_on_exceed=False)
    signal = _make_signal(probe)
    signal.do_line(global_limiter)
    assert signal.state is SignalState.SKIP_RATE_PROBE
