from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_RATE
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.internal.sampling import SpanSamplingRule

from ..utils import DummyTracer


def assert_sampling_decision_tags(span, sample_rate=1.0, mechanism=SamplingMechanism.SPAN_SAMPLING_RULE, limit=None):
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_RATE) == sample_rate
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == mechanism
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC) == limit


def traced_function(rule):
    tracer = DummyTracer()
    with tracer.trace("test_name") as span:
        span.service = "test_service"
        rule.sample(span)
    return span


def test_single_span_rule_default_no_match():
    rule = SpanSamplingRule()
    span = traced_function(rule)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_match():
    rule = SpanSamplingRule(service="test_service", name="test_name")
    span = traced_function(rule)

    assert_sampling_decision_tags(span)


def test_single_span_rule_match_max_per_sec():
    rule = SpanSamplingRule(service="test_service", name="test_name", max_per_second=133)
    span = traced_function(rule)

    assert_sampling_decision_tags(span, limit=133)


def test_multiple_span_rule_match():
    rule = SpanSamplingRule(service="test_service", name="test_name")
    for i in range(10):
        span = traced_function(rule)

        assert_sampling_decision_tags(span)


def test_rate_limiter_0():
    rule = SpanSamplingRule(service="test_service", name="test_name", max_per_second=0)
    for i in range(10):
        span = traced_function(rule)
        # None of the single span sampling tags should be added to the span
        # if it's dropped by the single span rate limiter
        assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)
