import inspect
import sys
import threading
from unittest import mock
from uuid import uuid4

from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.log import LogSignal
from ddtrace.debugging._signal.model import EvaluationError
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._signal.model import SignalTrack
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal._encoding import BufferFull
from tests.debugging.utils import create_log_function_probe
from tests.debugging.utils import create_snapshot_line_probe


def mock_encoder(wraps=None):
    encoder = mock.Mock(wraps=wraps)
    snapshot_encoder = mock.Mock()
    encoder._encoders = {Snapshot: snapshot_encoder}

    return encoder, snapshot_encoder


def _make_collector():
    encoder, _ = mock_encoder()
    return SignalCollector(tracks={SignalTrack.LOGS: encoder, SignalTrack.SNAPSHOT: encoder}), encoder


def _snapshot(state=SignalState.DONE):
    frame = inspect.currentframe()
    assert frame is not None
    s = Snapshot(
        probe=create_snapshot_line_probe(probe_id=uuid4(), source_file="file.py", line=1),
        frame=frame,
        thread=threading.current_thread(),
    )
    s.line({})
    s.state = state
    return s


def test_collector_collect_enqueue_only_commit_state():
    class MockLogSignal(LogSignal):
        def __init__(self, *args, **kwargs):
            super(MockLogSignal, self).__init__(*args, **kwargs)
            self.exit_call_count = 0
            self.enter_call_count = 0

        def enter(self, scope):
            self.enter_call_count += 1

        def exit(self, retval, exc_info, duration, scope):
            self.exit_call_count += 1

        def line(self, scope):
            return

        @property
        def message(self):
            return "test"

        def has_message(self):
            return True

    c = 0
    for i in range(10):
        mocked_signal = MockLogSignal(
            create_log_function_probe(
                probe_id="test",
                template=None,
                segments=[],
                module="foo",
                func_qname="bar",
                evaluate_at=ProbeEvalTiming.ENTRY,
            ),
            sys._getframe(),
            threading.current_thread(),
        )
        mocked_signal.do_enter()

        assert mocked_signal.enter_call_count == 1
        done = 1 - (i % 2)
        mocked_signal.state = SignalState.DONE if done else SignalState.SKIP_COND

        mocked_signal.do_exit(None, sys.exc_info(), 0)

        assert mocked_signal.exit_call_count == 0
        c += done

    assert c == 5


def test_collector_push_enqueue():
    encoder, _ = mock_encoder()

    collector = SignalCollector(tracks={SignalTrack.LOGS: encoder, SignalTrack.SNAPSHOT: encoder})
    frame = inspect.currentframe()
    assert frame is not None

    for _ in range(10):
        snapshot = Snapshot(
            probe=create_snapshot_line_probe(probe_id=uuid4(), source_file="file.py", line=123),
            frame=frame,
            thread=threading.current_thread(),
        )
        snapshot.line({})
        snapshot.state = SignalState.DONE
        collector.push(snapshot)

    assert len(encoder.put.mock_calls) == 10


def test_collector_push_buffer_full():
    """_enqueue should silently swallow BufferFull and increment the metric."""
    encoder, _ = mock_encoder()
    encoder.put.side_effect = BufferFull

    collector = SignalCollector(tracks={SignalTrack.LOGS: encoder, SignalTrack.SNAPSHOT: encoder})
    frame = inspect.currentframe()
    assert frame is not None

    snapshot = Snapshot(
        probe=create_snapshot_line_probe(probe_id=uuid4(), source_file="file.py", line=1),
        frame=frame,
        thread=threading.current_thread(),
    )
    snapshot.line({})
    snapshot.state = SignalState.DONE

    # Should not raise despite BufferFull
    collector.push(snapshot)
    encoder.put.assert_called_once()


def test_collector_push_unknown_track():
    """_enqueue should log an error when the signal track has no registered encoder."""
    # Provide an empty tracks dict so any track raises KeyError
    collector = SignalCollector(tracks={})
    frame = inspect.currentframe()
    assert frame is not None

    snapshot = Snapshot(
        probe=create_snapshot_line_probe(probe_id=uuid4(), source_file="file.py", line=1),
        frame=frame,
        thread=threading.current_thread(),
    )
    snapshot.line({})
    snapshot.state = SignalState.DONE

    # Should not raise despite missing track
    collector.push(snapshot)


# ---------------------------------------------------------------------------
# Canonical skip metric: reason codes
# ---------------------------------------------------------------------------


def test_push_skip_cond_error_emits_no_guardrail_metric():
    """evaluationErrorThrottled isn't part of the events.skipped reason
    vocabulary; no skipped metric should be emitted for SKIP_COND_ERROR.
    """
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.SKIP_COND_ERROR)

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    for call in m.increment.call_args_list:
        assert "dynamic_instrumentation.guardrails.events.skipped" not in call.args


def test_push_cond_timeout_emits_evaluation_timeout_and_uploads():
    """A timed-out condition is a skipped event, not an evaluation error, but
    its error message still reaches the user.
    """
    collector, encoder = _make_collector()
    signal = _snapshot(state=SignalState.COND_TIMEOUT)
    signal.errors.append(EvaluationError(expr="slow", message="Condition evaluation timed out after 20ms"))

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.increment.assert_any_call(
        "dynamic_instrumentation.guardrails.events.skipped",
        tags={
            "reason": "evaluationTimeout",
            "probe_id": signal.probe.probe_id,
            "event_type": "snapshot",
            "evaluation_kind": "condition",
        },
    )
    assert not any(
        c.args and c.args[0] == "dynamic_instrumentation.guardrails.evaluation.errors"
        for c in m.increment.call_args_list
    )
    encoder.put.assert_called_once_with(signal)


def test_push_skip_rate_global_emits_rate_limit_global():
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.SKIP_RATE_GLOBAL)

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.increment.assert_any_call(
        "dynamic_instrumentation.guardrails.events.skipped",
        tags={"reason": "rateLimitGlobal", "probe_id": signal.probe.probe_id, "event_type": "snapshot"},
    )


def test_push_skip_rate_probe_emits_rate_limit_probe():
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.SKIP_RATE_PROBE)

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.increment.assert_any_call(
        "dynamic_instrumentation.guardrails.events.skipped",
        tags={"reason": "rateLimitProbe", "probe_id": signal.probe.probe_id, "event_type": "snapshot"},
    )


def test_push_skip_budget_emits_no_guardrail_metric():
    """budgetExceededInvocation isn't part of the events.skipped reason
    vocabulary; no skipped metric should be emitted for SKIP_BUDGET.
    """
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.SKIP_BUDGET)

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    for call in m.increment.call_args_list:
        assert "dynamic_instrumentation.guardrails.events.skipped" not in call.args


def test_push_skip_cond_emits_no_guardrail_metric():
    """Condition-false is not a guardrail event; no skipped metric should be emitted."""
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.SKIP_COND)

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    # Verify no call to the skipped metric
    for call in m.increment.call_args_list:
        assert "dynamic_instrumentation.guardrails.events.skipped" not in call.args


def test_push_buffer_full_emits_queue_full_drop_metric():
    """BufferFull → canonical queueFull drop metric."""
    encoder, _ = mock_encoder()
    encoder.put.side_effect = BufferFull
    collector = SignalCollector(tracks={SignalTrack.LOGS: encoder, SignalTrack.SNAPSHOT: encoder})
    signal = _snapshot(state=SignalState.DONE)

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.increment.assert_any_call(
        "dynamic_instrumentation.guardrails.events.dropped",
        tags={"reason": "queueFull", "probe_id": signal.probe.probe_id, "event_type": "snapshot"},
    )


# ---------------------------------------------------------------------------
# Evaluation duration distribution metric
# ---------------------------------------------------------------------------


def test_push_emits_evaluation_duration_when_measured():
    """When _eval_duration_ms is set, a distribution metric is emitted."""
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.DONE)
    signal._eval_duration_ms = 3.5

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.distribution.assert_any_call(
        "dynamic_instrumentation.guardrails.evaluation.duration",
        3.5,
        tags={"probe_type": "LogLineProbe", "evaluation_kind": "condition"},
    )


def test_push_no_condition_evaluation_duration_when_not_measured():
    """When _eval_duration_ms is None, no condition evaluation.duration metric is emitted."""
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.DONE)
    assert signal._eval_duration_ms is None

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    condition_duration_calls = [
        c
        for c in m.distribution.call_args_list
        if c.args[0] == "dynamic_instrumentation.guardrails.evaluation.duration"
        and c.kwargs.get("tags", {}).get("evaluation_kind") == "condition"
    ]
    assert not condition_duration_calls, (
        "No condition evaluation.duration metric expected when _eval_duration_ms is None"
    )


# ---------------------------------------------------------------------------
# Capture and template eval duration distribution metrics
# ---------------------------------------------------------------------------


def test_push_emits_capture_duration_when_measured():
    """When _capture_duration_ms is set on a Snapshot, a distribution metric is emitted."""
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.DONE)
    signal._capture_duration_ms = 12.7

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    # truncated="false" because signal.errors is empty
    m.distribution.assert_any_call(
        "dynamic_instrumentation.guardrails.capture.duration",
        12.7,
        tags={"probe_type": "LogLineProbe", "truncated": "false"},
    )


def test_push_emits_template_eval_duration_when_measured():
    """When _template_eval_duration_ms is set on a Snapshot, an evaluation.duration metric is emitted."""
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.DONE)
    signal._template_eval_duration_ms = 2.1

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.distribution.assert_any_call(
        "dynamic_instrumentation.guardrails.evaluation.duration",
        2.1,
        tags={"probe_type": "LogLineProbe", "evaluation_kind": "template"},
    )


# ---------------------------------------------------------------------------
# capture.incomplete metric
# ---------------------------------------------------------------------------


def test_push_emits_capture_incomplete_for_template_timeout():
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.DONE)
    signal._segment_timed_out = True

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.increment.assert_any_call(
        "dynamic_instrumentation.guardrails.capture.incomplete",
        tags={
            "reason": "timeout",
            "probe_id": signal.probe.probe_id,
            "event_type": "snapshot",
            "evaluation_kind": "template",
        },
    )


def test_push_emits_capture_incomplete_for_capture_expression_error():
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.DONE)
    signal._capture_expr_error_reason = "runtimeError"

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.increment.assert_any_call(
        "dynamic_instrumentation.guardrails.capture.incomplete",
        tags={
            "reason": "runtimeError",
            "probe_id": signal.probe.probe_id,
            "event_type": "snapshot",
            "evaluation_kind": "capture_expression",
        },
    )


def test_push_emits_capture_incomplete_for_structural_limit():
    """A structural limit (depth/fieldCount/collectionSize/stringLength/other)
    carries no evaluation_kind -- it's not an expression evaluation outcome.
    """
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.DONE)
    signal._capture_incomplete_reason = "collectionSize"

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    m.increment.assert_any_call(
        "dynamic_instrumentation.guardrails.capture.incomplete",
        tags={"reason": "collectionSize", "probe_id": signal.probe.probe_id, "event_type": "snapshot"},
    )


def test_push_emits_no_capture_incomplete_when_complete():
    collector, _ = _make_collector()
    signal = _snapshot(state=SignalState.DONE)
    # _snapshot() captures the real test frame, whose own locals can
    # legitimately trip a structural limit (e.g. depth) -- clear the fields
    # explicitly so this test is isolated from that incidental noise.
    signal._segment_timed_out = False
    signal._capture_expr_error_reason = None
    signal._capture_incomplete_reason = None

    with mock.patch("ddtrace.debugging._signal.collector.meter") as m:
        collector.push(signal)

    for call in m.increment.call_args_list:
        assert "dynamic_instrumentation.guardrails.capture.incomplete" not in call.args
