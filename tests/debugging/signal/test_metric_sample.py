"""Tests for MetricSample value-expression error handling and timeouts."""

import inspect
import threading
import time
from unittest import mock

import pytest

from ddtrace.debugging._expressions import DDExpression
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import MetricProbeKind
from ddtrace.debugging._signal.metric_sample import MetricSample
from ddtrace.internal.settings.dynamic_instrumentation import config as di_config
from tests.debugging.utils import SLOW_SCOPE
from tests.debugging.utils import create_metric_line_probe
from tests.debugging.utils import slow_timed_expr


@pytest.fixture(autouse=True)
def _mock_meter():
    with mock.patch("ddtrace.debugging._signal.metric_sample.probe_metrics") as m:
        m.get_meter.return_value = mock.Mock()
        yield m


def _make_sample(probe):
    frame = inspect.currentframe()
    assert frame is not None
    return MetricSample(probe=probe, frame=frame, thread=threading.current_thread())


def _make_probe(value):
    return create_metric_line_probe(
        probe_id="test",
        source_file="test.py",
        line=1,
        kind=MetricProbeKind.COUNTER,
        name="test.counter",
        value=value,
    )


# ---------------------------------------------------------------------------
# Pre-existing gap: value-expression errors were entirely unhandled before
# this test file existed -- sample() would let a DDExpressionEvaluationError
# propagate straight out of do_enter()/do_exit()/do_line().
# ---------------------------------------------------------------------------


def test_value_eval_error_recorded_not_raised():
    """A bad value expression records an EvaluationError instead of
    propagating out of sample().
    """
    probe = _make_probe(DDExpression(dsl="missing", callable=dd_compile({"ref": "missing"})))
    sample = _make_sample(probe)

    sample.sample({})

    assert sample.errors, "Expected an EvaluationError to be recorded"


def test_value_eval_success_emits_metric(_mock_meter):
    probe = _make_probe(DDExpression(dsl="1", callable=dd_compile(1)))
    sample = _make_sample(probe)

    sample.sample({})

    assert not sample.errors
    _mock_meter.get_meter.return_value.increment.assert_called_once()


# ---------------------------------------------------------------------------
# Value-expression evaluation timeout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("call", ["sample", "enter"])
def test_slow_value_times_out(call, _mock_meter):
    """A value expression iterating a huge collection is stopped around
    evaluation_timeout_ms, records an error and emits no metric.
    """
    probe = _make_probe(slow_timed_expr())
    sample = _make_sample(probe)

    with mock.patch.object(di_config, "evaluation_timeout_ms", 20):
        start = time.monotonic()
        getattr(sample, call)(SLOW_SCOPE)
        elapsed = time.monotonic() - start

    assert [e.message for e in sample.errors] == ["Metric value evaluation timed out after 20ms"]
    _mock_meter.get_meter.return_value.increment.assert_not_called()
    assert elapsed < 1.0
