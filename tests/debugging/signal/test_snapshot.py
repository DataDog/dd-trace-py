"""Tests for Snapshot capture/template timing and config-driven timeouts."""

import inspect
import threading
from unittest import mock

from ddtrace.debugging._signal.snapshot import Snapshot
from tests.debugging.utils import compile_template
from tests.debugging.utils import create_log_line_probe
from tests.debugging.utils import create_snapshot_line_probe


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
