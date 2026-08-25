import mock
import pytest

from ddtrace._trace.filter_rule import FilterRule
from ddtrace._trace.filter_rule import parse_filtering_rules
from ddtrace.trace import Span
from tests.utils import scoped_tracer


def create_span(name="test.span", service=""):
    with scoped_tracer() as tracer:
        span = tracer.trace(name=name, service=service)
        span.finish()
        return span


def test_filter_rule_init_defaults():
    rule = FilterRule()
    assert rule.filter_rate == 1.0, "FilterRule rate should default to 1"
    assert rule.service is None, "FilterRule service should default to none"
    assert rule.name is None, "FilterRule name should default to none"


def test_filter_rule_init():
    rule = FilterRule(filter_rate=0.5, service="my-service", name="*request")
    assert rule.filter_rate == 0.5, "FilterRule should store the rate it's initialized with"
    assert rule.service.pattern == "my-service"
    assert rule.name.pattern == "*request"


@pytest.mark.parametrize(
    "rule_1,rule_2,expected_to_be_equal",
    [
        (FilterRule(), FilterRule(), True),
        (FilterRule(filter_rate=0.5), FilterRule(filter_rate=0.5), True),
        (FilterRule(filter_rate=0.5), FilterRule(filter_rate=1.0), False),
        (FilterRule(service="my-svc"), FilterRule(service="my-svc"), True),
        (FilterRule(service="my-svc"), FilterRule(service="other-svc"), False),
    ],
)
def test_filter_rule_eq(rule_1, rule_2, expected_to_be_equal):
    assert bool(rule_1 == rule_2) == expected_to_be_equal


@pytest.mark.parametrize(
    "span,rule,span_expected_to_match_rule",
    [
        (create_span(service="my-service"), FilterRule(service="my-*"), True),
        (create_span(service="my-service"), FilterRule(service="other-*"), False),
        (create_span(name="test.span"), FilterRule(name="test.span"), True),
        (create_span(name="test.span"), FilterRule(name="test_span"), False),
    ],
)
def test_filter_rule_matches(span, rule, span_expected_to_match_rule):
    assert rule.matches(span) is span_expected_to_match_rule


def test_filter_rule_should_drop_rate_1():
    rule = FilterRule(filter_rate=1)
    iterations = int(1e4)
    assert all(rule.should_drop(Span(name=str(i))) for i in range(iterations)), (
        "FilterRule with filter_rate=1 should always drop"
    )


def test_filter_rule_should_drop_rate_0():
    rule = FilterRule(filter_rate=0)
    iterations = int(1e4)
    assert sum(rule.should_drop(Span(name=str(i))) for i in range(iterations)) == 0, (
        "FilterRule with filter_rate=0 should never drop"
    )


@pytest.mark.subprocess()
def test_filter_rule_should_drop_deterministic_rate():
    from ddtrace._trace.filter_rule import FilterRule
    from ddtrace.trace import Span

    for filter_rate in [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.991]:
        rule = FilterRule(filter_rate=filter_rate)

        iterations = int(1e4 / filter_rate)
        dropped = sum(rule.should_drop(Span(name=str(i))) for i in range(iterations))

        deviation = abs(dropped - (iterations * filter_rate)) / (iterations * filter_rate)
        assert deviation < 0.05, (
            "Actual drop rate should be within 5 percent of set filter "
            "rate (actual: %f, set: %f, dropped count: %f)" % (deviation, filter_rate, dropped)
        )


def test_parse_filtering_rules_empty():
    assert parse_filtering_rules("") == []


def test_parse_filtering_rules_default_rate():
    rules = parse_filtering_rules('[{"service":"xyz","name":"abc"}]')
    assert len(rules) == 1
    assert rules[0].filter_rate == 1.0, "filter_rate should default to 1.0 when omitted"
    assert rules[0].service.pattern == "xyz"
    assert rules[0].name.pattern == "abc"


def test_parse_filtering_rules_explicit_rate():
    rules = parse_filtering_rules('[{"filter_rate":0.5,"service":"my-service","name":"my-name"}]')
    assert len(rules) == 1
    assert rules[0].filter_rate == 0.5
    assert rules[0].service.pattern == "my-service"
    assert rules[0].name.pattern == "my-name"


def test_parse_filtering_rules_multiple():
    rules = parse_filtering_rules('[{"filter_rate":1.0,"service":"xyz"}, {"filter_rate":0.5,"service":"my-service"}]')
    assert len(rules) == 2
    assert rules[0].filter_rate == 1.0
    assert rules[0].service.pattern == "xyz"
    assert rules[1].filter_rate == 0.5
    assert rules[1].service.pattern == "my-service"


def test_parse_filtering_rules_invalid_json():
    with mock.patch("ddtrace._trace.filter_rule.log") as mock_log:
        rules = parse_filtering_rules("not valid json")
    assert rules == []
    assert mock_log.error.called
