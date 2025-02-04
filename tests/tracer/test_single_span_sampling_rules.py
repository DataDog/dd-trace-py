import sys

import pytest

from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_RATE
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.internal.sampling import SpanSamplingRule
from ddtrace.internal.sampling import _get_file_json
from ddtrace.internal.sampling import get_span_sampling_rules
from tests.utils import DummyTracer

from ..utils import override_global_config


def traced_function(rule, tracer=None, name="test_name", service="test_service", trace_sampling=False):
    if not tracer:
        tracer = DummyTracer()
    with tracer.trace(name) as span:
        span.service = service
        # If the trace sampler samples the trace, then we shouldn't add the span sampling tags
        # because trace sampling takes precedence over single-span sampling
        if trace_sampling:
            span.context.sampling_priority = 1
        else:
            span.context.sampling_priority = 0
            if rule.match(span):
                rule.sample(span)
    return span


def assert_sampling_decision_tags(
    span, sample_rate=1.0, mechanism=SamplingMechanism.SPAN_SAMPLING_RULE, limit=None, trace_sampling=False
):
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_RATE) == sample_rate
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == mechanism
    assert span.get_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC) == limit

    if trace_sampling:
        assert span.get_metric(_SAMPLING_PRIORITY_KEY) > 0


def test_single_rule_init_via_env():
    with override_global_config(
        dict(_sampling_rules='[{"sample_rate":0.5,"service":"xyz","name":"abc","max_per_second":100}]')
    ):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._sample_rate == 0.5
        assert sampling_rules[0]._service_matcher.pattern == "xyz"
        assert sampling_rules[0]._name_matcher.pattern == "abc"
        assert sampling_rules[0]._max_per_second == 100
        assert len(sampling_rules) == 1


def test_multiple_rules_init_via_env():
    with override_global_config(
        dict(
            _sampling_rules='[{"service":"xy?","name":"a*c"}, \
            {"sample_rate":0.5,"service":"my-service","name":"my-name", "max_per_second":20}]'
        )
    ):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._sample_rate == 1.0
        assert sampling_rules[0]._service_matcher.pattern == "xy?"
        assert sampling_rules[0]._name_matcher.pattern == "a*c"
        assert sampling_rules[0]._max_per_second == -1

        assert sampling_rules[1]._sample_rate == 0.5
        assert sampling_rules[1]._service_matcher.pattern == "my-service"
        assert sampling_rules[1]._name_matcher.pattern == "my-name"
        assert sampling_rules[1]._max_per_second == 20
        assert len(sampling_rules) == 2


def test_rule_init_via_env_no_name():
    with override_global_config(dict(_sampling_rules='[{"service":"xyz", "sample_rate":0.23}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._sample_rate == 0.23
        assert sampling_rules[0]._service_matcher.pattern == "xyz"
        assert sampling_rules[0]._max_per_second == -1
        assert len(sampling_rules) == 1


def test_rule_init_via_env_only_name():
    with override_global_config(dict(_sampling_rules='[{"name":"xyz"}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._sample_rate == 1.0
        assert sampling_rules[0]._name_matcher.pattern == "xyz"
        assert sampling_rules[0]._max_per_second == -1
        assert len(sampling_rules) == 1


def test_rule_init_via_env_no_name_or_service():
    with override_global_config(dict(_sampling_rules='[{"sample_rate":1.0}]')):
        assert get_span_sampling_rules() == []


def test_rule_init_via_env_service_pattern_contains_unsupported_char():
    with override_global_config(dict(_sampling_rules='[{"service":"h[!a]i"}]')):
        assert get_span_sampling_rules() == []


def test_rule_init_via_env_name_pattern_contains_unsupported_char():
    with override_global_config(dict(_sampling_rules='[{"name":"h[!a]i"}]')):
        assert get_span_sampling_rules() == []


def test_rule_init_via_env_json_not_list():
    with override_global_config(
        dict(_sampling_rules='{"sample_rate":0.5,"service":"xyz","name":"abc","max_per_second":100}')
    ):
        assert get_span_sampling_rules() == []


def test_rule_init_via_env_json_not_valid():
    with override_global_config(
        dict(_sampling_rules='{"sample_rate":0.5,"service":"xyz","name":"abc","max_per_second":100')
    ):
        assert get_span_sampling_rules() == []


def test_env_rules_cause_matching_span_to_be_sampled():
    """Test that single span sampling tags are applied to spans that should get sampled when envars set"""
    with override_global_config(dict(_sampling_rules='[{"service":"test_service","name":"test_name"}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._service_matcher.pattern == "test_service"
        assert sampling_rules[0]._name_matcher.pattern == "test_name"
        tracer = DummyTracer()
        span = traced_function(sampling_rules[0], tracer=tracer)
        assert_sampling_decision_tags(span)


def test_env_rules_dont_cause_non_matching_span_to_be_sampled():
    """Test that single span sampling tags are not applied to spans that do not match rules"""
    with override_global_config(dict(_sampling_rules='[{"service":"test_ser","name":"test_na"}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._service_matcher.pattern == "test_ser"
        assert sampling_rules[0]._name_matcher.pattern == "test_na"
        tracer = DummyTracer()
        span = traced_function(sampling_rules[0], tracer=tracer)
        assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_single_span_rules_not_applied_when_span_sampled_by_trace_sampling():
    """Test that single span sampling rules aren't applied if a span is already going to be sampled by trace sampler"""
    with override_global_config(dict(_sampling_rules='[{"service":"test_service","name":"test_name"}]')):
        sampling_rules = get_span_sampling_rules()
        assert sampling_rules[0]._service_matcher.pattern == "test_service"
        assert sampling_rules[0]._name_matcher.pattern == "test_name"
        tracer = DummyTracer()
        span = traced_function(sampling_rules[0], tracer=tracer, trace_sampling=True)
        assert sampling_rules[0].match(span) is True
        assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None, trace_sampling=True)


def test_sampling_rule_init_config_multiple_sampling_rule_json_via_file(tmpdir):
    file = tmpdir.join("rules.json")
    file.write(
        '[{"service":"xy?","name":"a*c"}, \
            {"sample_rate":0.5,"service":"my-service","name":"my-name", "max_per_second":"20"}]'
    )

    with override_global_config(dict(_sampling_rules_file=str(file))):
        sampling_rules = _get_file_json()
        assert sampling_rules == [
            {"service": "xy?", "name": "a*c"},
            {"sample_rate": 0.5, "service": "my-service", "name": "my-name", "max_per_second": "20"},
        ]

    with override_global_config(dict(_sampling_rules_file="data/this_doesnt_exist.json")):
        exception = FileNotFoundError if sys.version_info.major > 3 else IOError
        with pytest.raises(exception):
            get_span_sampling_rules()


def test_env_config_takes_precedence_over_file_config(tmpdir, caplog):
    file = tmpdir.join("rules.json")
    file.write('[{"sample_rate":1.0,"service":"x","name":"ab","max_per_second":1000}]')

    with override_global_config(
        dict(
            _sampling_rules='[{"sample_rate":0.5,"service":"xyz","name":"abc","max_per_second":100}]',
            _sampling_rules_file=str(file),
        )
    ):
        sampling_rules = get_span_sampling_rules()
        assert caplog.record_tuples == [
            (
                "ddtrace.internal.sampling",
                30,
                "DD_SPAN_SAMPLING_RULES and DD_SPAN_SAMPLING_RULES_FILE detected. "
                "Defaulting to DD_SPAN_SAMPLING_RULES value.",
            )
        ]
        assert sampling_rules[0]._sample_rate == 0.5
        assert sampling_rules[0]._service_matcher.pattern == "xyz"
        assert sampling_rules[0]._name_matcher.pattern == "abc"
        assert sampling_rules[0]._max_per_second == 100
        assert len(sampling_rules) == 1


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


# NB more extensive testing of the matching is done in test_glob_matcher.py
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
    tracer = DummyTracer()
    for _ in range(10):
        span = traced_function(rule, tracer)
        assert_sampling_decision_tags(span)


def test_single_span_rules_not_applied_if_span_dropped_by_single_span_rate_limiter():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=0)
    tracer = DummyTracer()
    for _ in range(10):
        span = traced_function(rule, tracer)
        assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)


def test_max_per_sec_with_is_allowed_check():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=2)
    # Make spans till we hit the limit, then make a span while the limit is hit and make sure tags were not added.
    tracer = DummyTracer(rule)
    while True:
        span = traced_function(rule, tracer)
        if not rule._limiter.is_allowed():
            break
        assert_sampling_decision_tags(span, limit=2)

    rate_limited_span = traced_function(rule, tracer)
    assert_sampling_decision_tags(rate_limited_span, sample_rate=None, mechanism=None, limit=None)


def test_max_per_sec_with_predetermined_number_of_spans():
    rule = SpanSamplingRule(service="test_service", name="test_name", sample_rate=1.0, max_per_second=2)
    tracer = DummyTracer()
    for i in range(3):
        span = traced_function(rule, tracer)
        if i < 2:
            assert_sampling_decision_tags(span, limit=2)
        else:
            assert_sampling_decision_tags(span, sample_rate=None, mechanism=None, limit=None)
