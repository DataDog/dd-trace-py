import pytest
import unittest
from ddtrace.single_span_sampling_rule import SpanSamplingRule, SAMPLING_MECHANISM
from ..utils import DummyTracer
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM, _SINGLE_SPAN_SAMPLING_MAX_PER_SEC, _SINGLE_SPAN_SAMPLING_RATE
from ddtrace import Span

def assert_sampling_decision_tags(span, sample_rate=1.0, mechanism=SAMPLING_MECHANISM, limit=None):
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

    assert_sampling_decision_tags(span, sample_rate=None, mechanism=None)

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
    spans = []
    for i in range(10):
        span = (traced_function(rule))
        assert_sampling_decision_tags(span)
