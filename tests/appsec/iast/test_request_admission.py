from random import Random

import pytest

from ddtrace._trace.sampler import RateSampler
from ddtrace.appsec._iast._overhead_control_engine import OverheadControl
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.internal.constants import SAMPLING_HASH_MODULO
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR
from ddtrace.internal.native._native import SpanData
from tests.utils import override_global_config


def _admission_at_percentage(percentage):
    control = OverheadControl()
    with override_global_config(dict(_iast_request_sampling=percentage)):
        control.reconfigure()
    return control


@pytest.mark.subprocess(
    env=dict(DD_IAST_ENABLED="false", DD_APPSEC_ENABLED="false"),
    parametrize=dict(DD_IAST_REQUEST_SAMPLING=("0", "50", "100")),
    err=None,
)
def test_request_admission_initial_percentage():
    import os

    from ddtrace.appsec._iast._overhead_control_engine import oce
    from ddtrace.internal.native._native import SpanData

    percentage = os.environ["DD_IAST_REQUEST_SAMPLING"]
    rejected_at_half = SpanData(name="request", trace_id=10, span_id=1)
    accepted_at_half = SpanData(name="request", trace_id=8696342848850656916, span_id=1)
    assert oce.acquire_request(rejected_at_half) is (percentage == "100")
    assert oce.acquire_request(accepted_at_half) is (percentage != "0")


@pytest.mark.parametrize(
    "percentage",
    [-10.0, 0.0, 0.001, 1.0, 30.0, 50.0, 99.999, 100.0, 110.0, "30.0", float("inf"), -float("inf"), float("nan")],
)
def test_request_admission_preserves_sampler_decisions(percentage):
    control = _admission_at_percentage(percentage)
    reference = RateSampler(sample_rate=float(percentage) / 100.0)
    rng = Random(0)
    trace_ids = [1, 2, 10, 1 << 63, (1 << 64) - 1, 1 << 64, 1 << 127, (1 << 128) - 1]
    trace_ids.extend(rng.getrandbits(64) for _ in range(512))
    trace_ids.extend(rng.getrandbits(128) for _ in range(512))

    with override_global_config(dict(_iast_debug=False)):
        for trace_id in trace_ids:
            span = SpanData(name="request", trace_id=trace_id, span_id=1)
            assert control.acquire_request(span) == reference.sample(span), (percentage, trace_id)


@pytest.mark.parametrize(
    "trace_id, expected",
    [
        (6645412625895797507, False),
        (8696342848850656916, True),
        (14629469446186818297, False),
        (13794769880582338323, True),
        (18444899399302180861, False),
        (18446744073709551615, False),
        (10, False),
    ],
)
def test_request_admission_known_trace_ids(trace_id, expected):
    control = _admission_at_percentage(50.0)
    span = SpanData(name="request", trace_id=trace_id, span_id=1)
    assert control.acquire_request(span) is expected


@pytest.mark.parametrize("offset, expected", [(-1, True), (0, True), (1, False)])
def test_request_admission_inclusive_threshold(offset, expected):
    # Invert the hash to exercise values immediately around the 50% threshold.
    inverse = pow(SAMPLING_KNUTH_FACTOR, -1, SAMPLING_HASH_MODULO)
    trace_id = (((1 << 63) + offset) * inverse) % SAMPLING_HASH_MODULO
    span = SpanData(name="request", trace_id=trace_id, span_id=1)
    assert _admission_at_percentage(50.0).acquire_request(span) is expected


def test_request_admission_zero_rate_preserves_zero_hash():
    control = _admission_at_percentage(0.0)
    span = SpanData(name="request", trace_id=1 << 64, span_id=1)
    assert span._trace_id_64bits == 0
    assert control.acquire_request(span) is True
    assert control.acquire_request(SpanData(name="request", trace_id=1, span_id=1)) is False


@pytest.mark.parametrize("percentage", [-10.0, 0.0, 30.0, 100.0, 110.0])
def test_request_admission_without_span(percentage):
    assert _admission_at_percentage(percentage).acquire_request(None) is True


@pytest.mark.parametrize("low_bits", [10, 8696342848850656916])
def test_request_admission_uses_trace_identity(low_bits):
    control = _admission_at_percentage(50.0)
    expected = control.acquire_request(SpanData(name="request", trace_id=low_bits, span_id=1))
    for high_bits in [0, 1, (1 << 64) - 1]:
        for span_id in [1, 2, (1 << 64) - 1]:
            span = SpanData(name="request", trace_id=(high_bits << 64) | low_bits, span_id=span_id)
            assert control.acquire_request(span) is expected
            assert control.acquire_request(span) is expected


def test_request_admission_reconfigure():
    control = _admission_at_percentage(100.0)
    span = SpanData(name="request", trace_id=1, span_id=1)
    assert control.acquire_request(span) is True

    with override_global_config(dict(_iast_request_sampling=0.0)):
        assert control.acquire_request(span) is True
        control.reconfigure()
        assert control.acquire_request(span) is False

    with override_global_config(dict(_iast_request_sampling=100.0)):
        assert control.acquire_request(span) is False
        control.reconfigure()
        assert control.acquire_request(span) is True


def test_request_admission_instances_reconfigure_independently():
    rejecting = _admission_at_percentage(0.0)
    accepting = _admission_at_percentage(100.0)
    span = SpanData(name="request", trace_id=1, span_id=1)
    assert rejecting.acquire_request(span) is False
    assert accepting.acquire_request(span) is True


@pytest.mark.parametrize("percentage, expected", [(0.0, False), (100.0, True)])
@pytest.mark.parametrize("priority", [USER_REJECT, USER_KEEP])
def test_request_admission_leaves_trace_retention_unchanged(percentage, expected, priority):
    control = _admission_at_percentage(percentage)
    span = SpanData(name="request", trace_id=1, span_id=1)
    context = span.context
    context._publish_sampling_decision(priority, 0.25, True)
    span._set_attribute("test.tag", "unchanged")
    attributes = dict(span._get_attributes())
    decision_maker = context._meta.get("_dd.p.dm")
    sampling_state = context._otel_sampling_state_data

    assert control.acquire_request(span) is expected

    assert span.context is context
    assert context.sampling_priority == priority
    assert context._meta.get("_dd.p.dm") == decision_maker
    assert context._otel_sampling_state_data == sampling_state
    assert dict(span._get_attributes()) == attributes


def test_request_admission_debug_message(caplog):
    control = _admission_at_percentage(0.0)
    with override_global_config(dict(_iast_debug=True)):
        assert control.acquire_request(SpanData(name="request", trace_id=1, span_id=1)) is False
    assert "Skip request by sampling rate" in caplog.text
