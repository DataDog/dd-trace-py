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


def traced_function(rule, name="test_name", service="test_service"):
    tracer = DummyTracer()
    with tracer.trace(name) as span:
        span.service = service
        if rule.match(span):
            rule.sample(span)
    return span


def test_single_span_rule_no_match_empty_strings():
    rule = SpanSamplingRule(service="", name="", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)
    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_no_match_service():
    rule = SpanSamplingRule(service="wrong_service_name", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_no_match_name():
    rule = SpanSamplingRule(service="test_service", name="wrong_operation_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_no_match_only_service():
    rule = SpanSamplingRule(service="wrong_service_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_no_match_no_span_name_or_service():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule, name=None, service=None)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_only_span_service():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule, name=None)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_only_span_name():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule, service=None)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_no_match_only_name():
    rule = SpanSamplingRule(name="wrong_operation_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)
    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_match():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span)


def test_single_span_rule_match_only_service():
    rule = SpanSamplingRule(service="test_service", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span)


def test_single_span_rule_match_only_name():
    rule = SpanSamplingRule(name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span)


# More extensive testing of the matching is done in test_glob_matcher.py
def test_single_span_rule_match_wildcards():
    rule = SpanSamplingRule(service="test_*", name="test?????", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span)


def test_single_span_rule_no_match_wildcards():
    rule = SpanSamplingRule(service="*test_", name="test_nam??", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_unsupported_pattern_bracket_expansion():
    rule = SpanSamplingRule(service="test_servic[a-z]+", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_unsupported_pattern_escape_character():
    rule = SpanSamplingRule(service="test_servic[?]", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule)

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rule_unsupported_pattern_bracket_expansion_literal_evaluation():
    rule = SpanSamplingRule(service="test_servic[a-z]+", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule, service="test_servic[a-z]+")

    assert_sampling_decision_tags(span)


def test_single_span_rule_unsupported_pattern_escape_character_literal_evaluation():
    rule = SpanSamplingRule(service="test_servic[?]", name="test_name", sample_rate=1.0, max_per_second=-1)
    span = traced_function(rule, service="test_servic[?]")

    assert_sampling_decision_tags(span)


def test_multiple_span_rule_match():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=-1)
    for i in range(10):
        span = traced_function(rule)

        assert_sampling_decision_tags(span)


def test_max_per_sec_0():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=0)
    for i in range(10):
        span = traced_function(rule)
        # None of the single span sampling tags should be added to the span
        # if it's dropped by the single span rate limiter
        assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_match_max_per_sec():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=2)
    # Make spans till we hit the limit, then make a span while the limit is hit and make sure tags were not added.
    while True:
        span = traced_function(rule)
        if rule._limiter._is_allowed(span.start_ns):
            assert_sampling_decision_tags(span, limit=2)
        else:
            break

    rate_limited_span = traced_function(rule)

    assert_sampling_decision_tags(rate_limited_span, sample_rate=None, mechanism=None, limit=None)
