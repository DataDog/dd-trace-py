from __future__ import division

import re
import unittest

import mock
import pytest

from ddtrace.compat import iteritems
from ddtrace.constants import SAMPLE_RATE_METRIC_KEY
from ddtrace.constants import SAMPLING_AGENT_DECISION
from ddtrace.constants import SAMPLING_LIMIT_DECISION
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import SAMPLING_RULE_DECISION
from ddtrace.ext.priority import AUTO_KEEP
from ddtrace.ext.priority import AUTO_REJECT
from ddtrace.internal.rate_limiter import RateLimiter
from ddtrace.sampler import AllSampler
from ddtrace.sampler import DatadogSampler
from ddtrace.sampler import RateByServiceSampler
from ddtrace.sampler import RateSampler
from ddtrace.sampler import SamplingRule
from ddtrace.span import Span

from ..utils import DummyTracer
from ..utils import override_env


@pytest.fixture
def dummy_tracer():
    return DummyTracer()


def assert_sampling_decision_tags(span, agent=None, limit=None, rule=None):
    assert span.get_metric(SAMPLING_AGENT_DECISION) == agent
    assert span.get_metric(SAMPLING_LIMIT_DECISION) == limit
    assert span.get_metric(SAMPLING_RULE_DECISION) == rule


def create_span(tracer=None, name="test.span", service=""):
    tracer = tracer or DummyTracer()
    span = tracer.trace(name=name, service=service)
    span.finish()
    return span


class RateSamplerTest(unittest.TestCase):
    def test_set_sample_rate(self):
        sampler = RateSampler()
        assert sampler.sample_rate == 1.0

        for rate in [0.001, 0.01, 0.1, 0.25, 0.5, 0.75, 0.99999999, 1.0, 1]:
            sampler.set_sample_rate(rate)
            assert sampler.sample_rate == float(rate)

            sampler.set_sample_rate(str(rate))
            assert sampler.sample_rate == float(rate)

    def test_set_sample_rate_str(self):
        sampler = RateSampler()
        sampler.set_sample_rate("0.5")
        assert sampler.sample_rate == 0.5

    def test_sample_rate_deviation(self):
        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = DummyTracer()

            tracer.sampler = RateSampler(sample_rate)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = tracer.pop()

            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) == sample_rate

            # Less than 5% deviation when 'enough' iterations (arbitrary, just check if it converges)
            deviation = abs(len(samples) - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.05, "Deviation too high %f with sample_rate %f" % (deviation, sample_rate)

    def test_deterministic_behavior(self):
        """ Test that for a given trace ID, the result is always the same """
        tracer = DummyTracer()

        tracer.sampler = RateSampler(0.5)

        for i in range(10):
            span = tracer.trace(i)
            span.finish()

            samples = tracer.pop()
            assert len(samples) <= 1, "there should be 0 or 1 spans"
            sampled = 1 == len(samples)
            for j in range(10):
                other_span = Span(tracer, i, trace_id=span.trace_id)
                assert sampled == tracer.sampler.sample(
                    other_span
                ), "sampling should give the same result for a given trace_id"

    def test_negative_sample_rate_raises_error(self):
        tracer = DummyTracer()
        with pytest.raises(ValueError, match="sample_rate of -0.5 is negative"):
            tracer.sampler = RateSampler(sample_rate=-0.5)

    def test_sample_rate_0_does_not_reset_to_1(self):
        # Regression test for case where a sample rate of 0 caused the sample rate to be reset to 1
        tracer = DummyTracer()
        tracer.sampler = RateSampler(sample_rate=0)
        assert tracer.sampler.sample_rate == 0


class RateByServiceSamplerTest(unittest.TestCase):
    def test_default_key(self):
        assert (
            "service:,env:" == RateByServiceSampler._default_key
        ), "default key should correspond to no service and no env"

    def test_key(self):
        assert RateByServiceSampler._default_key == RateByServiceSampler._key()
        assert "service:mcnulty,env:" == RateByServiceSampler._key(service="mcnulty")
        assert "service:,env:test" == RateByServiceSampler._key(env="test")
        assert "service:mcnulty,env:test" == RateByServiceSampler._key(service="mcnulty", env="test")
        assert "service:mcnulty,env:test" == RateByServiceSampler._key("mcnulty", "test")

    def test_sample_rate_deviation(self):
        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = DummyTracer()
            writer = tracer.writer
            tracer.configure(sampler=AllSampler())
            # We need to set the writer because tracer.configure overrides it,
            # indeed, as we enable priority sampling, we must ensure the writer
            # is priority sampling aware and pass it a reference on the
            # priority sampler to send the feedback it gets from the agent
            assert writer != tracer.writer, "writer should have been updated by configure"
            tracer.writer = writer
            tracer.priority_sampler.set_sample_rate(sample_rate)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = tracer.pop()
            samples_with_high_priority = 0
            for sample in samples:
                if sample.get_metric(SAMPLING_PRIORITY_KEY) is not None:
                    if sample.get_metric(SAMPLING_PRIORITY_KEY) > 0:
                        samples_with_high_priority += 1
                else:
                    assert 0 == sample.get_metric(
                        SAMPLING_PRIORITY_KEY
                    ), "when priority sampling is on, priority should be 0 when trace is to be dropped"
                assert_sampling_decision_tags(sample, agent=sample_rate)
            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) is None

            # Less than 5% deviation when 'enough' iterations (arbitrary, just check if it converges)
            deviation = abs(samples_with_high_priority - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.05, "Deviation too high %f with sample_rate %f" % (deviation, sample_rate)

    def test_update_rate_by_service_sample_rates(self):
        cases = [
            {
                "service:,env:": 1,
            },
            {
                "service:,env:": 1,
                "service:mcnulty,env:dev": 0.33,
                "service:postgres,env:dev": 0.7,
            },
            {
                "service:,env:": 1,
                "service:mcnulty,env:dev": 0.25,
                "service:postgres,env:dev": 0.5,
                "service:redis,env:prod": 0.75,
            },
        ]

        tracer = DummyTracer()
        tracer.configure(sampler=AllSampler())
        priority_sampler = tracer.priority_sampler
        for case in cases:
            priority_sampler.update_rate_by_service_sample_rates(case)
            rates = {}
            for k, v in iteritems(priority_sampler._by_service_samplers):
                rates[k] = v.sample_rate
            assert case == rates, "%s != %s" % (case, rates)
        # It's important to also test in reverse mode for we want to make sure key deletion
        # works as well as key insertion (and doing this both ways ensures we trigger both cases)
        cases.reverse()
        for case in cases:
            priority_sampler.update_rate_by_service_sample_rates(case)
            rates = {}
            for k, v in iteritems(priority_sampler._by_service_samplers):
                rates[k] = v.sample_rate
            assert case == rates, "%s != %s" % (case, rates)


@pytest.mark.parametrize(
    "sample_rate,allowed",
    [
        # Min/max allowed values
        (0.0, True),
        (1.0, True),
        # Accepted boundaries
        (0.000001, True),
        (0.999999, True),
        # Outside the bounds
        (-0.000000001, False),
        (1.0000000001, False),
    ]
    + [
        # Try a bunch of decimal values between 0 and 1
        (1 / i, True)
        for i in range(1, 50)
    ]
    + [
        # Try a bunch of decimal values less than 0
        (-(1 / i), False)
        for i in range(1, 50)
    ]
    + [
        # Try a bunch of decimal values greater than 1
        (1 + (1 / i), False)
        for i in range(1, 50)
    ],
)
def test_sampling_rule_init_sample_rate(sample_rate, allowed):
    if allowed:
        rule = SamplingRule(sample_rate=sample_rate)
        assert rule.sample_rate == sample_rate
    else:
        with pytest.raises(ValueError):
            SamplingRule(sample_rate=sample_rate)


def test_sampling_rule_init_defaults():
    rule = SamplingRule(sample_rate=1.0)
    assert rule.sample_rate == 1.0
    assert rule.service == SamplingRule.NO_RULE
    assert rule.name == SamplingRule.NO_RULE


def test_sampling_rule_init():
    name_regex = re.compile(r"\.request$")

    rule = SamplingRule(
        sample_rate=0.0,
        # Value
        service="my-service",
        # Regex
        name=name_regex,
    )

    assert rule.sample_rate == 0.0
    assert rule.service == "my-service"
    assert rule.name == name_regex


@pytest.mark.parametrize(
    "span,rule,expected",
    [
        # DEV: Use sample_rate=1 to ensure SamplingRule._sample always returns True
        (create_span(name=name), SamplingRule(sample_rate=1, name=pattern), expected)
        for name, pattern, expected in [
            ("test.span", SamplingRule.NO_RULE, True),
            # DEV: `span.name` cannot be `None`
            ("test.span", None, False),
            ("test.span", "test.span", True),
            ("test.span", "test_span", False),
            ("test.span", re.compile(r"^test\.span$"), True),
            ("test_span", re.compile(r"^test.span$"), True),
            ("test.span", re.compile(r"^test_span$"), False),
            ("test.span", re.compile(r"test"), True),
            ("test.span", re.compile(r"test\.span|another\.span"), True),
            ("another.span", re.compile(r"test\.span|another\.span"), True),
            ("test.span", lambda name: "span" in name, True),
            ("test.span", lambda name: "span" not in name, False),
            ("test.span", lambda name: 1 / 0, False),
        ]
    ],
)
def test_sampling_rule_matches_name(span, rule, expected):
    assert rule.matches(span) is expected, "{} -> {} -> {}".format(rule, span, expected)


@pytest.mark.parametrize(
    "span,rule,expected",
    [
        # DEV: Use sample_rate=1 to ensure SamplingRule._sample always returns True
        (create_span(service=service), SamplingRule(sample_rate=1, service=pattern), expected)
        for service, pattern, expected in [
            ("my-service", SamplingRule.NO_RULE, True),
            ("my-service", None, False),
            (None, None, True),
            (None, "my-service", False),
            (None, re.compile(r"my-service"), False),
            (None, lambda service: "service" in service, False),
            ("my-service", "my-service", True),
            ("my-service", "my_service", False),
            ("my-service", re.compile(r"^my-"), True),
            ("my_service", re.compile(r"^my[_-]"), True),
            ("my-service", re.compile(r"^my_"), False),
            ("my-service", re.compile(r"my-service"), True),
            ("my-service", re.compile(r"my"), True),
            ("my-service", re.compile(r"my-service|another-service"), True),
            ("another-service", re.compile(r"my-service|another-service"), True),
            ("my-service", lambda service: "service" in service, True),
            ("my-service", lambda service: "service" not in service, False),
            ("my-service", lambda service: 1 / 0, False),
        ]
    ],
)
def test_sampling_rule_matches_service(span, rule, expected):
    assert rule.matches(span) is expected, "{} -> {} -> {}".format(rule, span, expected)


@pytest.mark.parametrize(
    "span,rule,expected",
    [
        # All match
        (
            create_span(
                name="test.span",
                service="my-service",
            ),
            SamplingRule(
                sample_rate=1,
                name="test.span",
                service=re.compile(r"^my-"),
            ),
            True,
        ),
        # All match,  but sample rate of 0%
        # DEV: We are checking if it is a match, not computing sampling rate, sample_rate=0 is not considered
        (
            create_span(
                name="test.span",
                service="my-service",
            ),
            SamplingRule(
                sample_rate=0,
                name="test.span",
                service=re.compile(r"^my-"),
            ),
            True,
        ),
        # Name doesn't match
        (
            create_span(
                name="test.span",
                service="my-service",
            ),
            SamplingRule(
                sample_rate=1,
                name="test_span",
                service=re.compile(r"^my-"),
            ),
            False,
        ),
        # Service doesn't match
        (
            create_span(
                name="test.span",
                service="my-service",
            ),
            SamplingRule(
                sample_rate=1,
                name="test.span",
                service=re.compile(r"^service-"),
            ),
            False,
        ),
    ],
)
def test_sampling_rule_matches(span, rule, expected):
    assert rule.matches(span) is expected, "{} -> {} -> {}".format(rule, span, expected)


def test_sampling_rule_matches_exception():
    e = Exception("an error occurred")

    def pattern(prop):
        raise e

    rule = SamplingRule(sample_rate=1.0, name=pattern)
    span = create_span(name="test.span")

    with mock.patch("ddtrace.sampler.log") as mock_log:
        assert rule.matches(span) is False
        mock_log.warning.assert_called_once_with(
            "%r pattern %r failed with %r",
            rule,
            pattern,
            "test.span",
            exc_info=True,
        )


@pytest.mark.parametrize("sample_rate", [0.01, 0.1, 0.15, 0.25, 0.5, 0.75, 0.85, 0.9, 0.95, 0.991])
def test_sampling_rule_sample(sample_rate):
    tracer = DummyTracer()
    rule = SamplingRule(sample_rate=sample_rate)

    iterations = int(1e4 / sample_rate)
    sampled = sum(rule.sample(Span(tracer=tracer, name=i)) for i in range(iterations))

    # Less than 5% deviation when 'enough' iterations (arbitrary, just check if it converges)
    deviation = abs(sampled - (iterations * sample_rate)) / (iterations * sample_rate)
    assert deviation < 0.05, "Deviation {!r} too high with sample_rate {!r} for {} sampled".format(
        deviation, sample_rate, sampled
    )


def test_sampling_rule_sample_rate_1():
    tracer = DummyTracer()
    rule = SamplingRule(sample_rate=1)

    iterations = int(1e4)
    assert all(rule.sample(Span(tracer=tracer, name=i)) for i in range(iterations))


def test_sampling_rule_sample_rate_0():
    tracer = DummyTracer()
    rule = SamplingRule(sample_rate=0)

    iterations = int(1e4)
    assert sum(rule.sample(Span(tracer=tracer, name=i)) for i in range(iterations)) == 0


def test_datadog_sampler_init():
    # No args
    sampler = DatadogSampler()
    assert sampler.rules == []
    assert isinstance(sampler.limiter, RateLimiter)
    assert sampler.limiter.rate_limit == DatadogSampler.DEFAULT_RATE_LIMIT
    assert isinstance(sampler.default_sampler, RateByServiceSampler)

    # With rules
    rule = SamplingRule(sample_rate=1)
    sampler = DatadogSampler(rules=[rule])
    assert sampler.rules == [rule]
    assert sampler.limiter.rate_limit == DatadogSampler.DEFAULT_RATE_LIMIT
    assert isinstance(sampler.default_sampler, RateByServiceSampler)

    # With rate limit
    sampler = DatadogSampler(rate_limit=10)
    assert sampler.limiter.rate_limit == 10
    assert isinstance(sampler.default_sampler, RateByServiceSampler)

    # With default_sample_rate
    sampler = DatadogSampler(default_sample_rate=0.5)
    assert sampler.limiter.rate_limit == DatadogSampler.DEFAULT_RATE_LIMIT
    assert isinstance(sampler.default_sampler, SamplingRule)
    assert sampler.default_sampler.sample_rate == 0.5

    # From env variables
    with override_env(dict(DD_TRACE_SAMPLE_RATE="0.5", DD_TRACE_RATE_LIMIT="10")):
        sampler = DatadogSampler()
        assert sampler.limiter.rate_limit == 10
        assert isinstance(sampler.default_sampler, SamplingRule)
        assert sampler.default_sampler.sample_rate == 0.5

    # Invalid env vars
    with override_env(dict(DD_TRACE_SAMPLE_RATE="asdf")):
        with pytest.raises(ValueError):
            DatadogSampler()

    # Invalid env vars
    with override_env(dict(DD_TRACE_RATE_LIMIT="asdf")):
        with pytest.raises(ValueError):
            DatadogSampler()

    # Invalid rules
    for val in (None, True, False, object(), 1, Exception()):
        with pytest.raises(TypeError):
            DatadogSampler(rules=[val])

    # Ensure rule order
    rule_1 = SamplingRule(sample_rate=1)
    rule_2 = SamplingRule(sample_rate=0.5, service="test")
    rule_3 = SamplingRule(sample_rate=0.25, name="flask.request")
    sampler = DatadogSampler(rules=[rule_1, rule_2, rule_3])
    assert sampler.rules == [rule_1, rule_2, rule_3]


@mock.patch("ddtrace.sampler.RateByServiceSampler.sample")
def test_datadog_sampler_sample_no_rules(mock_sample, dummy_tracer):
    sampler = DatadogSampler()
    dummy_tracer.configure(sampler=sampler)

    # Default RateByServiceSampler() is applied
    #   No rules configured
    #   No global rate limit
    #   No rate limit configured
    # RateByServiceSampler.sample(span) returns True
    mock_sample.return_value = True
    with dummy_tracer.trace("test"):
        pass
    spans = dummy_tracer.pop()
    assert len(spans) == 1, "Span should have been written"
    assert spans[0].get_metric(SAMPLING_PRIORITY_KEY) is AUTO_KEEP

    # Default RateByServiceSampler() is applied
    #   No rules configured
    #   No global rate limit
    #   No rate limit configured
    # RateByServiceSampler.sample(span) returns False
    mock_sample.return_value = False
    with dummy_tracer.trace("test"):
        pass
    spans = dummy_tracer.pop()
    assert len(spans) == 1, "Span should have been written"
    assert spans[0].get_metric(SAMPLING_PRIORITY_KEY) is AUTO_REJECT


class MatchSample(SamplingRule):
    def matches(self, span):
        return True

    def sample(self, span):
        return True


class NoMatch(SamplingRule):
    def matches(self, span):
        return False

    def sample(self, span):
        return True


class MatchNoSample(SamplingRule):
    def matches(self, span):
        return True

    def sample(self, span):
        return False


@pytest.mark.parametrize(
    "sampler, sampling_priority, rule, limit",
    [
        (
            DatadogSampler(
                default_sample_rate=1.0,
                rules=[
                    NoMatch(0.5),
                    NoMatch(0.5),
                    NoMatch(0.5),
                ],
            ),
            AUTO_KEEP,
            1.0,
            1.0,
        ),
        (
            DatadogSampler(
                default_sample_rate=1.0,
                rules=[
                    NoMatch(0.5),
                    NoMatch(0.5),
                    MatchSample(0.5),
                ],
            ),
            AUTO_KEEP,
            0.5,
            1.0,
        ),
        (
            DatadogSampler(
                default_sample_rate=1.0,
                rules=[
                    MatchSample(0.5),
                    MatchNoSample(0.5),
                    MatchNoSample(0.5),
                ],
            ),
            AUTO_KEEP,
            0.5,
            1.0,
        ),
        (
            DatadogSampler(
                default_sample_rate=1.0,
                rules=[
                    NoMatch(0.5),
                    MatchNoSample(0.5),
                    NoMatch(0.5),
                ],
            ),
            AUTO_REJECT,
            0.5,
            None,
        ),
        (
            DatadogSampler(
                default_sample_rate=1.0,
                rules=[
                    NoMatch(0.5),
                    MatchNoSample(0.5),
                    NoMatch(0.5),
                ],
            ),
            AUTO_REJECT,
            0.5,
            None,
        ),
    ],
)
def test_datadog_sampler_sample_rules(sampler, sampling_priority, rule, limit, dummy_tracer):
    dummy_tracer.configure(sampler=sampler)
    with dummy_tracer.trace("span"):
        pass
    spans = dummy_tracer.pop()

    # A span is always expected as DatadogSamplers are used.
    assert len(spans) > 0
    span = spans[0]
    # Hence, it should always be "sampled" (sent to the agent)
    # This is an implementation detail so we probably don't have to
    # test it.
    assert span.sampled
    assert span.get_metric(SAMPLING_LIMIT_DECISION) == limit
    assert span.get_metric(SAMPLING_RULE_DECISION) == rule


def test_datadog_sampler_tracer(dummy_tracer):
    rule = SamplingRule(sample_rate=1.0, name="test.span")
    rule_spy = mock.Mock(spec=rule, wraps=rule)
    rule_spy.sample_rate = rule.sample_rate

    sampler = DatadogSampler(rules=[rule_spy])
    limiter_spy = mock.Mock(spec=sampler.limiter, wraps=sampler.limiter)
    sampler.limiter = limiter_spy
    sampler_spy = mock.Mock(spec=sampler, wraps=sampler)

    dummy_tracer.configure(sampler=sampler_spy)

    assert dummy_tracer.sampler is sampler_spy

    with dummy_tracer.trace("test.span") as span:
        # Assert all of our expected functions were called
        sampler_spy.sample.assert_called_once_with(span)
        rule_spy.matches.assert_called_once_with(span)
        rule_spy.sample.assert_called_once_with(span)
        limiter_spy.is_allowed.assert_called_once_with()

    spans = dummy_tracer.pop()
    assert len(spans) == 1, "Span should have been sampled and written"
    assert spans[0].get_metric(SAMPLING_PRIORITY_KEY) is AUTO_KEEP
    assert spans[0].get_metric(SAMPLING_RULE_DECISION) == 1.0


def test_datadog_sampler_tracer_rate_limited(dummy_tracer):
    rule = SamplingRule(sample_rate=1.0, name="test.span")
    rule_spy = mock.Mock(spec=rule, wraps=rule)
    rule_spy.sample_rate = rule.sample_rate

    sampler = DatadogSampler(rules=[rule_spy])
    limiter_spy = mock.Mock(spec=sampler.limiter, wraps=sampler.limiter)
    limiter_spy.is_allowed.return_value = False  # Have the limiter deny the span
    sampler.limiter = limiter_spy
    sampler_spy = mock.Mock(spec=sampler, wraps=sampler)

    dummy_tracer.configure(sampler=sampler_spy)

    assert dummy_tracer.sampler is sampler_spy

    with dummy_tracer.trace("test.span") as span:
        # Assert all of our expected functions were called
        sampler_spy.sample.assert_called_once_with(span)
        rule_spy.matches.assert_called_once_with(span)
        rule_spy.sample.assert_called_once_with(span)
        limiter_spy.is_allowed.assert_called_once_with()

    spans = dummy_tracer.pop()
    assert len(spans) == 1, "Span should have been sampled and written"
    assert spans[0].get_metric(SAMPLING_PRIORITY_KEY) is AUTO_REJECT
    assert spans[0].get_metric(SAMPLING_LIMIT_DECISION) is None
    assert spans[0].get_metric(SAMPLING_RULE_DECISION) == 1.0


def test_datadog_sampler_tracer_rate_0(dummy_tracer):
    rule = SamplingRule(sample_rate=0, name="test.span")  # Sample rate of 0 means never sample
    rule_spy = mock.Mock(spec=rule, wraps=rule)
    rule_spy.sample_rate = rule.sample_rate

    sampler = DatadogSampler(rules=[rule_spy])
    limiter_spy = mock.Mock(spec=sampler.limiter, wraps=sampler.limiter)
    sampler.limiter = limiter_spy
    sampler_spy = mock.Mock(spec=sampler, wraps=sampler)

    dummy_tracer.configure(sampler=sampler_spy)

    assert dummy_tracer.sampler is sampler_spy

    with dummy_tracer.trace("test.span") as span:
        # Assert all of our expected functions were called
        sampler_spy.sample.assert_called_once_with(span)
        rule_spy.matches.assert_called_once_with(span)
        rule_spy.sample.assert_called_once_with(span)
        limiter_spy.is_allowed.assert_not_called()

    spans = dummy_tracer.pop()
    assert len(spans) == 1, "Span should have been sampled and written"
    assert spans[0].get_metric(SAMPLING_PRIORITY_KEY) is AUTO_REJECT
    assert spans[0].get_metric(SAMPLING_RULE_DECISION) == 0


def test_datadog_sampler_tracer_child(dummy_tracer):
    rule = SamplingRule(sample_rate=1.0)  # No rules means it gets applied to every span
    rule_spy = mock.Mock(spec=rule, wraps=rule)
    rule_spy.sample_rate = rule.sample_rate

    sampler = DatadogSampler(rules=[rule_spy])
    limiter_spy = mock.Mock(spec=sampler.limiter, wraps=sampler.limiter)
    sampler.limiter = limiter_spy
    sampler_spy = mock.Mock(spec=sampler, wraps=sampler)

    dummy_tracer.configure(sampler=sampler_spy)

    assert dummy_tracer.sampler is sampler_spy

    with dummy_tracer.trace("parent.span") as parent:
        with dummy_tracer.trace("child.span"):
            # Assert all of our expected functions were called
            # DEV: `assert_called_once_with` ensures we didn't also call with the child span
            sampler_spy.sample.assert_called_once_with(parent)
            rule_spy.matches.assert_called_once_with(parent)
            rule_spy.sample.assert_called_once_with(parent)
            limiter_spy.is_allowed.assert_called_once_with()

    spans = dummy_tracer.pop()
    assert len(spans) == 2, "Trace should have been sampled and written"
    assert spans[0].get_metric(SAMPLING_PRIORITY_KEY) is AUTO_KEEP
    assert spans[0].get_metric(SAMPLING_RULE_DECISION) == 1.0


def test_datadog_sampler_tracer_start_span(dummy_tracer):
    rule = SamplingRule(sample_rate=1.0)  # No rules means it gets applied to every span
    rule_spy = mock.Mock(spec=rule, wraps=rule)
    rule_spy.sample_rate = rule.sample_rate

    sampler = DatadogSampler(rules=[rule_spy])
    limiter_spy = mock.Mock(spec=sampler.limiter, wraps=sampler.limiter)
    sampler.limiter = limiter_spy
    sampler_spy = mock.Mock(spec=sampler, wraps=sampler)

    dummy_tracer.configure(sampler=sampler_spy)

    assert dummy_tracer.sampler is sampler_spy

    span = dummy_tracer.start_span("test.span")
    span.finish()

    # Assert all of our expected functions were called
    sampler_spy.sample.assert_called_once_with(span)
    rule_spy.matches.assert_called_once_with(span)
    rule_spy.sample.assert_called_once_with(span)
    limiter_spy.is_allowed.assert_called_once_with()

    spans = dummy_tracer.pop()
    assert len(spans) == 1, "Span should have been sampled and written"
    assert spans[0].get_metric(SAMPLING_PRIORITY_KEY) is AUTO_KEEP
    assert spans[0].get_metric(SAMPLING_RULE_DECISION) == 1.0


def test_datadog_sampler_update_rate_by_service_sample_rates(dummy_tracer):
    cases = [
        {
            "service:,env:": 1,
        },
        {
            "service:,env:": 1,
            "service:mcnulty,env:dev": 0.33,
            "service:postgres,env:dev": 0.7,
        },
        {
            "service:,env:": 1,
            "service:mcnulty,env:dev": 0.25,
            "service:postgres,env:dev": 0.5,
            "service:redis,env:prod": 0.75,
        },
    ]

    # By default sampler sets it's default sampler to RateByServiceSampler
    sampler = DatadogSampler()
    for case in cases:
        sampler.update_rate_by_service_sample_rates(case)
        rates = {}
        for k, v in iteritems(sampler.default_sampler._by_service_samplers):
            rates[k] = v.sample_rate
        assert case == rates, "%s != %s" % (case, rates)

    # It's important to also test in reverse mode for we want to make sure key deletion
    # works as well as key insertion (and doing this both ways ensures we trigger both cases)
    cases.reverse()
    for case in cases:
        sampler.update_rate_by_service_sample_rates(case)
        rates = {}
        for k, v in iteritems(sampler.default_sampler._by_service_samplers):
            rates[k] = v.sample_rate
        assert case == rates, "%s != %s" % (case, rates)
