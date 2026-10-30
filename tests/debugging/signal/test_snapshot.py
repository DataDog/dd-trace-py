"""Tests for Snapshot capture/template timing and config-driven timeouts."""

import inspect
import threading
import time
from unittest import mock

from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import CaptureExpression
from ddtrace.debugging._probe.model import ExpressionTemplateSegment
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._redaction import DDTimedRedactedExpression
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal.settings.dynamic_instrumentation import config as di_config
from tests.debugging.utils import SLOW_SCOPE
from tests.debugging.utils import compile_template
from tests.debugging.utils import create_capture_expressions_line_probe
from tests.debugging.utils import create_log_line_probe
from tests.debugging.utils import create_snapshot_line_probe
from tests.debugging.utils import slow_timed_expr


def _make_snapshot(probe):
    frame = inspect.currentframe()
    assert frame is not None
    return Snapshot(probe=probe, frame=frame, thread=threading.current_thread())


# ---------------------------------------------------------------------------
# Timing fields are populated
# ---------------------------------------------------------------------------


def test_capture_duration_ms_populated_after_line():
    """_capture_duration_ms is set after a line-probe snapshot capture."""
    probe = create_snapshot_line_probe(probe_id="test", source_file="test.py", line=1)
    snap = _make_snapshot(probe)
    snap.line({})
    assert snap._capture_duration_ms is not None
    assert snap._capture_duration_ms >= 0.0


def test_template_eval_duration_ms_populated_after_line():
    """_template_eval_duration_ms is set after template evaluation."""
    t = compile_template("hello world")
    probe = create_log_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        **t,
    )
    snap = _make_snapshot(probe)
    snap.line({})
    assert snap._template_eval_duration_ms is not None
    assert snap._template_eval_duration_ms >= 0.0


def test_capture_duration_ms_none_for_no_capture_probe():
    """A log probe without snapshot/capture_expressions leaves _capture_duration_ms as None."""
    t = compile_template("msg")
    probe = create_log_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        **t,
    )
    snap = _make_snapshot(probe)
    snap.line({})
    assert snap._capture_duration_ms is None


# ---------------------------------------------------------------------------
# Config-driven capture timeout
# ---------------------------------------------------------------------------


def test_capture_uses_config_timeout():
    """HourGlass duration in _capture_context should reflect di_config.capture_timeout_ms."""
    probe = create_snapshot_line_probe(probe_id="test", source_file="test.py", line=1)
    snap = _make_snapshot(probe)

    hourglass_durations = []

    import ddtrace.debugging._signal.snapshot as snap_module

    original_HourGlass = snap_module.HourGlass

    class TrackingHourGlass(original_HourGlass):
        def turn(self):
            hourglass_durations.append(self._duration)
            super().turn()

    with mock.patch.object(snap_module, "HourGlass", TrackingHourGlass):
        with mock.patch("ddtrace.debugging._signal.snapshot.di_config") as cfg:
            cfg.capture_timeout_ms = 75
            cfg.evaluation_timeout_ms = 10
            snap.line({})

    assert hourglass_durations, "HourGlass was not instantiated"
    assert all(d == 0.075 for d in hourglass_durations), f"Expected capture timeout 0.075s, got: {hourglass_durations}"


# ---------------------------------------------------------------------------
# Template eval timeout records an EvaluationError
# ---------------------------------------------------------------------------


def test_template_eval_timeout_records_error():
    """When template evaluation exceeds the budget, an EvaluationError is appended."""
    t = compile_template("hello")
    probe = create_log_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        **t,
    )
    snap = _make_snapshot(probe)

    with mock.patch("ddtrace.debugging._signal.snapshot.di_config") as cfg:
        cfg.capture_timeout_ms = 150
        cfg.evaluation_timeout_ms = -1  # any positive elapsed time exceeds budget
        snap.line({})

    timeout_errors = [e for e in snap.errors if "exceeded budget" in e.message]
    assert timeout_errors, "Expected a timeout EvaluationError for template evaluation"


# ---------------------------------------------------------------------------
# Template-segment evaluation timeout
# ---------------------------------------------------------------------------


def test_slow_segment_times_out():
    """A template segment iterating a huge collection is stopped around
    evaluation_timeout_ms, and the rest of the template still renders.
    """
    probe = create_log_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        template="before {slow} after",
        segments=[
            LiteralTemplateSegment("before "),
            ExpressionTemplateSegment(slow_timed_expr()),
            LiteralTemplateSegment(" after"),
        ],
    )
    snap = _make_snapshot(probe)

    with mock.patch.object(di_config, "evaluation_timeout_ms", 20):
        start = time.monotonic()
        snap.line(SLOW_SCOPE)
        elapsed = time.monotonic() - start

    assert snap._message == "before ERROR after"
    # Reported once, not again by the post-hoc overrun check.
    assert [e.message for e in snap.errors] == ["Segment evaluation timed out after 20ms"]
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# Capture-expression evaluation errors/timeouts don't kill the whole event
# ---------------------------------------------------------------------------


def _bad_capture_expression(name="bad"):
    def _raise(scope):
        raise ZeroDivisionError("boom")

    return CaptureExpression(
        name=name, expr=DDTimedRedactedExpression(dsl=name, callable=_raise), capture=DEFAULT_CAPTURE_LIMITS
    )


def _good_capture_expression(name="good", value=1):
    return CaptureExpression(
        name=name,
        expr=DDTimedRedactedExpression(dsl=name, callable=lambda scope: value),
        capture=DEFAULT_CAPTURE_LIMITS,
    )


def _slow_capture_expression(name="slow"):
    return CaptureExpression(name=name, expr=slow_timed_expr(name), capture=DEFAULT_CAPTURE_LIMITS)


def test_capture_expression_runtime_error_does_not_kill_event():
    """A capture expression that raises is recorded as an error and marked
    notCapturedReason=runtimeError, but other expressions still evaluate and
    the event still carries a message/captures.
    """
    probe = create_capture_expressions_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        capture_expressions=[_good_capture_expression(), _bad_capture_expression()],
    )
    snap = _make_snapshot(probe)
    snap.line({})

    captured = snap.line_capture["captureExpressions"]
    assert captured["good"]["value"] == "1"
    assert captured["bad"] == {"notCapturedReason": "runtimeError"}
    assert snap._capture_expr_error_reason == "runtimeError"
    assert snap.errors, "Expected an EvaluationError for the failing capture expression"


def test_capture_expression_timeout_does_not_kill_event():
    """A capture expression that times out is marked notCapturedReason=timeout
    without preventing the rest of the event from being captured.
    """
    probe = create_capture_expressions_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        capture_expressions=[_good_capture_expression(), _slow_capture_expression()],
    )
    snap = _make_snapshot(probe)

    with mock.patch.object(di_config, "evaluation_timeout_ms", 20):
        start = time.monotonic()
        snap.line(SLOW_SCOPE)
        elapsed = time.monotonic() - start

    captured = snap.line_capture["captureExpressions"]
    assert captured["good"]["value"] == "1"
    assert captured["slow"] == {"notCapturedReason": "timeout"}
    assert snap._capture_expr_error_reason == "timeout"
    timeout_errors = [e for e in snap.errors if "timed out" in e.message]
    assert timeout_errors
    assert elapsed < 1.0


def test_capture_expression_structural_limit_sets_incomplete_reason():
    """A capture expression whose value overruns a structural limit sets
    _capture_incomplete_reason (no evaluation_kind -- it's not an evaluation
    failure, just a value too big to capture in full).
    """
    probe = create_capture_expressions_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        capture_expressions=[_good_capture_expression(value=list(range(1000)))],
    )
    snap = _make_snapshot(probe)
    snap.line({})

    assert snap._capture_incomplete_reason == "collectionSize"
    assert snap._capture_expr_error_reason is None


def test_capture_expression_complete_capture_sets_no_incomplete_reason():
    probe = create_capture_expressions_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        capture_expressions=[_good_capture_expression()],
    )
    snap = _make_snapshot(probe)
    snap.line({})

    assert snap._capture_incomplete_reason is None
    assert snap._capture_expr_error_reason is None
