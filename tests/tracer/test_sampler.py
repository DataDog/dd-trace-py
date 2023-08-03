from __future__ import division

import re
import unittest

import mock
import pytest

from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import SAMPLE_RATE_METRIC_KEY
from ddtrace.constants import SAMPLING_AGENT_DECISION
from ddtrace.constants import SAMPLING_LIMIT_DECISION
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import SAMPLING_RULE_DECISION
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.context import Context
from ddtrace.internal.compat import iteritems
from ddtrace.internal.rate_limiter import RateLimiter
from ddtrace.internal.sampling import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.internal.sampling import update_sampling_decision
from ddtrace.sampler import AllSampler
from ddtrace.sampler import DatadogSampler
from ddtrace.sampler import RateByServiceSampler
from ddtrace.sampler import RateSampler
from ddtrace.sampler import SamplingRule
from ddtrace.span import Span

from ..subprocesstest import run_in_subprocess
from ..utils import DummyTracer
from ..utils import override_env
from ..utils import override_global_config


@pytest.fixture
def dummy_tracer():
    return DummyTracer()


def assert_sampling_decision_tags(
    span,
    agent=None,
    limit=None,
    rule=None,
    sampling_priority=None,
    trace_tag=None,
):
    """Check span attribute given an expected sampling decision

    :param agent: expected agent rate ``_dd.agent_psr``
    :param limit: expected rate limit ``_dd.limit_psr``
    :param rule: expected sampler rule rate ``_dd.rule_psr``
    :param sampling_priority: expected sampling priority ``_sampling_priority_v1``
    :param trace_tag: expected sampling decision trace tag ``_dd.p.dm``. Format is ``-{SAMPLINGMECHANISM}``.
    """
    metric_agent = span.get_metric(SAMPLING_AGENT_DECISION)
    metric_limit = span.get_metric(SAMPLING_LIMIT_DECISION)
    metric_rule = span.get_metric(SAMPLING_RULE_DECISION)
    metric_sampling_priority = span.get_metric(SAMPLING_PRIORITY_KEY)
    if agent:
        assert metric_agent == agent
    if limit:
        assert metric_limit == limit
    if rule:
        assert metric_rule == rule
    if sampling_priority:
        assert metric_sampling_priority == sampling_priority

    if trace_tag:
        assert span.context._meta[SAMPLING_DECISION_TRACE_TAG_KEY] == trace_tag
    else:
        assert SAMPLING_DECISION_TRACE_TAG_KEY not in span.context._meta


def create_span(tracer=None, name="test.span", service=""):
    tracer = tracer or DummyTracer()
    span = tracer.trace(name=name, service=service)
    span.finish()
    return span


class RateSamplerTest(unittest.TestCase):
    def test_set_sample_rate(self):
        sampler = RateSampler()
        assert sampler.sample_rate == 1.0, "RateSampler rate should default to 1.0"

        for rate in [0.001, 0.01, 0.1, 0.25, 0.5, 0.75, 0.99999999, 1.0, 1]:
            sampler.set_sample_rate(rate)
            assert sampler.sample_rate == float(rate), "Setting the rate on a RateSampler should work"

            sampler.set_sample_rate(str(rate))
            assert sampler.sample_rate == float(rate), "The rate can be set as a string"

    def test_sample_rate_deviation(self):
        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = DummyTracer()

            tracer._sampler = RateSampler(sample_rate)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(str(i))
                span.finish()

            samples = tracer.pop()

            assert (
                samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) == sample_rate
            ), "Sampled span should have sample rate properly assigned"

            deviation = abs(len(samples) - (iterations * sample_rate)) / (iterations * sample_rate)
            assert (
                deviation < 0.05
            ), "Actual sample rate should be within 5 percent of set sample " "rate (actual: %f, set: %f)" % (
                deviation,
                sample_rate,
            )

    def test_deterministic_behavior(self):
        """Test that for a given trace ID, the result is always the same"""
        tracer = DummyTracer()

        tracer._sampler = RateSampler(0.5)

        for i in range(10):
            span = tracer.trace(str(i))
            span.finish()

            samples = tracer.pop()
            assert (
                len(samples) <= 1
            ), "evaluating sampling rules against a span should result in either dropping or not dropping it"
            sampled = 1 == len(samples)
            for j in range(10):
                other_span = Span(str(i), trace_id=span.trace_id)
                assert sampled == tracer._sampler.sample(
                    other_span
                ), "sampling should give the same result for a given trace_id"

    def test_negative_sample_rate_raises_error(self):
        tracer = DummyTracer()
        with pytest.raises(ValueError, match="sample_rate of -0.5 is negative"):
            tracer._sampler = RateSampler(sample_rate=-0.5)

    def test_sample_rate_0_does_not_reset_to_1(self):
        tracer = DummyTracer()
        tracer._sampler = RateSampler(sample_rate=0)
        assert (
            tracer._sampler.sample_rate == 0
        ), "Setting the sample rate to zero should result in the sample rate being zero"


class RateByServiceSamplerTest(unittest.TestCase):
    def test_default_key(self):
        assert (
            "service:,env:" == RateByServiceSampler._default_key
        ), "default key should correspond to no service and no env"

    def test_key(self):
        assert (
            RateByServiceSampler._default_key == RateByServiceSampler._key()
        ), "_key() with no arguments returns the default key"
        assert "service:mcnulty,env:" == RateByServiceSampler._key(
            service="mcnulty"
        ), "_key call with service name returns expected result"
        assert "service:,env:test" == RateByServiceSampler._key(
            env="test"
        ), "_key call with env name returns expected result"
        assert "service:mcnulty,env:test" == RateByServiceSampler._key(
            service="mcnulty", env="test"
        ), "_key call with service and env name returns expected result"
        assert "service:mcnulty,env:test" == RateByServiceSampler._key(
            "mcnulty", "test"
        ), "_key call with service and env name as positional args returns expected result"

    @run_in_subprocess(env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="true"))
    def test_sample_rate_deviation_128bit_trace_id(self):
        self._test_sample_rate_deviation()

    @run_in_subprocess(env=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="false"))
    def test_sample_rate_deviation_64bit_trace_id(self):
        self._test_sample_rate_deviation()

    def _test_sample_rate_deviation(self):
        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = DummyTracer()
            writer = tracer._writer
            tracer.configure(sampler=AllSampler())
            non_dummy_writer_would_have_priority_sampler = (
                tracer._writer is not writer and tracer._priority_sampler is not None
            )
            assert (
                non_dummy_writer_would_have_priority_sampler
            ), "After configure() with a sampler argument, the tracer's writer should have a priority sampler"
            tracer._priority_sampler.set_sample_rate(sample_rate)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(str(i))
                span.finish()

            samples = tracer._writer.pop()
            samples_with_high_priority = 0
            for sample in samples:
                sample_priority = sample.get_metric(SAMPLING_PRIORITY_KEY)
                if sample_priority is not None:
                    samples_with_high_priority += int(bool(sample_priority > 0))
                assert_sampling_decision_tags(
                    sample,
                    agent=sample_rate,
                    trace_tag="-{}".format(SamplingMechanism.AGENT_RATE),
                )
            assert (
                samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) is None
            ), "A sampled span should not have the SAMPLE_RATE_METRIC_KEY tag set"

            deviation = abs(samples_with_high_priority - (iterations * sample_rate)) / (iterations * sample_rate)
            assert (
                deviation < 0.05
            ), "Actual sample rate should be within 5 percent of set sample " "rate (actual: %f, set: %f)" % (
                deviation,
                sample_rate,
            )


@pytest.mark.parametrize(
    "sample_rate,sample_rate_is_valid",
    [
        (0.0, True),
        (1.0, True),
        (0.000001, True),
        (0.999999, True),
        (-0.000000001, False),
        (1.0000000001, False),
    ]
    + [(1 / i, True) for i in range(1, 50)]
    + [(-(1 / i), False) for i in range(1, 50)]
    + [(1 + (1 / i), False) for i in range(1, 50)],
)
def test_sampling_rule_init_sample_rate(sample_rate, sample_rate_is_valid):
    if sample_rate_is_valid:
        rule = SamplingRule(sample_rate=sample_rate)
        assert rule.sample_rate == sample_rate, "SamplingRule should store the rate it's initialized with"
    else:
        with pytest.raises(ValueError):
            SamplingRule(sample_rate=sample_rate)


def test_sampling_rule_init_defaults():
    rule = SamplingRule(sample_rate=1.0)
    assert rule.sample_rate == 1.0, "SamplingRule rate should default to 1"
    assert rule.service == SamplingRule.NO_RULE, "SamplingRule service should default to none"
    assert rule.name == SamplingRule.NO_RULE, "SamplingRule name should default to none"


def test_sampling_rule_init():
    a_regex = re.compile(r"\.request$")
    a_string = "my-service"

    rule = SamplingRule(
        sample_rate=0.0,
        service=a_string,
        name=a_regex,
    )

    assert rule.sample_rate == 0.0, "SamplingRule should store the rate it's initialized with"
    assert rule.service == a_string, "SamplingRule should store the service it's initialized with"
    assert rule.name == a_regex, "SamplingRule should store the name regex it's initialized with"


@pytest.mark.parametrize(
    "rule_1,rule_2,expected_to_be_equal",
    [
        (SamplingRule(sample_rate=1.0), SamplingRule(sample_rate=1.0), True),
        (SamplingRule(sample_rate=0.5), SamplingRule(sample_rate=0.5), True),
        (SamplingRule(sample_rate=0.0), SamplingRule(sample_rate=0.0), True),
        (SamplingRule(sample_rate=0.5), SamplingRule(sample_rate=1.0), False),
        (SamplingRule(sample_rate=1.0, service="my-svc"), SamplingRule(sample_rate=1.0, service="my-svc"), True),
        (
            SamplingRule(sample_rate=1.0, service=re.compile("my-svc")),
            SamplingRule(sample_rate=1.0, service=re.compile("my-svc")),
            True,
        ),
        (SamplingRule(sample_rate=1.0, service="my-svc"), SamplingRule(sample_rate=1.0, service="other-svc"), False),
        (SamplingRule(sample_rate=1.0, service="my-svc"), SamplingRule(sample_rate=0.5, service="my-svc"), False),
        (
            SamplingRule(sample_rate=1.0, service=re.compile("my-svc")),
            SamplingRule(sample_rate=0.5, service=re.compile("my-svc")),
            False,
        ),
        (
            SamplingRule(sample_rate=1.0, service=re.compile("my-svc")),
            SamplingRule(sample_rate=1.0, service=re.compile("other")),
            False,
        ),
        (
            SamplingRule(sample_rate=1.0, name="span.name"),
            SamplingRule(sample_rate=1.0, name="span.name"),
            True,
        ),
        (
            SamplingRule(sample_rate=1.0, name=re.compile("span.name")),
            SamplingRule(sample_rate=1.0, name=re.compile("span.name")),
            True,
        ),
        (
            SamplingRule(sample_rate=1.0, name=re.compile("span.name")),
            SamplingRule(sample_rate=1.0, name=re.compile("span.other")),
            False,
        ),
        (
            SamplingRule(sample_rate=1.0, name="span.name"),
            SamplingRule(sample_rate=0.5, name="span.name"),
            False,
        ),
        (SamplingRule(sample_rate=1.0, name="span.name"), SamplingRule(sample_rate=1.0, name="span.other"), False),
        (SamplingRule(sample_rate=1.0, name="span.name"), SamplingRule(sample_rate=0.5, name="span.name"), False),
        (
            SamplingRule(sample_rate=1.0, service="my-svc", name="span.name"),
            SamplingRule(sample_rate=1.0, service="my-svc", name="span.name"),
            True,
        ),
        (
            SamplingRule(sample_rate=1.0, service="my-svc", name=re.compile("span.name")),
            SamplingRule(sample_rate=1.0, service="my-svc", name=re.compile("span.name")),
            True,
        ),
        (
            SamplingRule(sample_rate=1.0, service=re.compile("my-svc"), name=re.compile("span.name")),
            SamplingRule(sample_rate=1.0, service=re.compile("my-svc"), name=re.compile("span.name")),
            True,
        ),
        (
            SamplingRule(sample_rate=1.0, service="my-svc", name="span.name"),
            SamplingRule(sample_rate=0.5, service="my-svc", name="span.name"),
            False,
        ),
        (
            SamplingRule(sample_rate=1.0, service="my-svc", name="span.name"),
            SamplingRule(sample_rate=1.0, service="other", name="span.name"),
            False,
        ),
        (
            SamplingRule(sample_rate=1.0, service="my-svc", name="span.name"),
            SamplingRule(sample_rate=1.0, service="my-svc", name="span.other"),
            False,
        ),
    ],
)
def test_sampling_rule_eq(rule_1, rule_2, expected_to_be_equal):
    assert bool(rule_1 == rule_2) == expected_to_be_equal


def test_sampling_rule_init_via_env():
    with override_env(dict(DD_TRACE_SAMPLING_RULES='[{"sample_rate":1.0,"service":"xyz","name":"abc"}]')):
        sampling_rule = DatadogSampler().rules
        assert sampling_rule[0].sample_rate == 1.0, "DatadogSampler initializes one rule from envvar"
        assert sampling_rule[0].service == "xyz"
        assert sampling_rule[0].name == "abc"
        assert len(sampling_rule) == 1

    with override_env(
        dict(
            DD_TRACE_SAMPLING_RULES='[{"sample_rate":1.0,"service":"xyz","name":"abc"}, \
            {"sample_rate":0.5,"service":"my-service","name":"my-name"}]'
        )
    ):
        sampling_rule = DatadogSampler().rules
        assert sampling_rule[0].sample_rate == 1.0, "DatadogSampler initializes from envvar containing multiple rules"
        assert sampling_rule[0].service == "xyz"
        assert sampling_rule[0].name == "abc"

        assert sampling_rule[1].sample_rate == 0.5, "DatadogSampler initializes from envvar containing multiple rules"
        assert sampling_rule[1].service == "my-service"
        assert sampling_rule[1].name == "my-name"
        assert len(sampling_rule) == 2

    with override_env(dict(DD_TRACE_SAMPLING_RULES='[{"sample_rate":1.0}]')):
        sampling_rule = DatadogSampler().rules
        assert sampling_rule[0].sample_rate == 1.0, "DatadogSampler initializes from envvar with only sample_rate set"
        assert sampling_rule[0].service == SamplingRule.NO_RULE
        assert sampling_rule[0].name == SamplingRule.NO_RULE
        assert len(sampling_rule) == 1

    with override_env(dict(DD_TRACE_SAMPLING_RULES='[{"sample_rate":1.0,"service":"xyz"}]')):
        sampling_rule = DatadogSampler().rules
        assert sampling_rule[0].sample_rate == 1.0, "DatadogSampler initializes from envvar without name set"
        assert sampling_rule[0].service == "xyz"
        assert sampling_rule[0].name == SamplingRule.NO_RULE
        assert len(sampling_rule) == 1

    with override_env(dict(DD_TRACE_SAMPLING_RULES='[{"sample_rate":1.0,"name":"abc"}]')):
        sampling_rule = DatadogSampler().rules
        assert sampling_rule[0].sample_rate == 1.0, "DatadogSampler initializes from envvar without service set"
        assert sampling_rule[0].service == SamplingRule.NO_RULE
        assert sampling_rule[0].name == "abc"
        assert len(sampling_rule) == 1

    # The following error handling tests use assertions on the json items instead of the returned string due
    # to Python's undefined ordering of dictionary keys

    with pytest.raises(ValueError) as excinfo:
        with override_env(dict(DD_TRACE_SAMPLING_RULES='[{"sample_rate":2.0,"service":"xyz","name":"abc"}]')):
            sampling_rule = DatadogSampler().rules
    assert str(excinfo.value).endswith(
        "SamplingRule(sample_rate=2.0) must be greater than or equal to 0.0 and less than or equal to 1.0"
    )
    assert '"sample_rate": 2.0' in str(excinfo.value)
    assert '"service": "xyz"' in str(excinfo.value)
    assert '"name": "abc"' in str(excinfo.value)

    with pytest.raises(KeyError) as excinfo:
        with override_env(dict(DD_TRACE_SAMPLING_RULES='[{"service":"xyz","name":"abc"}]')):
            sampling_rule = DatadogSampler().rules
    assert str(excinfo.value).startswith("'No sample_rate provided for sampling rule: ")
    assert '"service": "xyz"' in str(excinfo.value)
    assert '"name": "abc"' in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        with override_env(dict(DD_TRACE_SAMPLING_RULES='["sample_rate":1.0,"service":"xyz","name":"abc"]')):
            sampling_rule = DatadogSampler().rules
    assert 'Unable to parse DD_TRACE_SAMPLING_RULES=["sample_rate":1.0,"service":"xyz","name":"abc"]' == str(
        excinfo.value
    )

    with pytest.raises(KeyError) as excinfo:
        with override_env(
            dict(
                DD_TRACE_SAMPLING_RULES='[{"sample_rate":1.0,"service":"xyz","name":"abc"},'
                + '{"service":"my-service","name":"my-name"}]'
            )
        ):
            sampling_rule = DatadogSampler().rules
    assert str(excinfo.value).startswith("'No sample_rate provided for sampling rule: ")
    assert '"service": "my-service"' in str(excinfo.value)
    assert '"name": "my-name"' in str(excinfo.value)


@pytest.mark.parametrize(
    "span,rule,span_expected_to_match_rule",
    [
        (create_span(name=name), SamplingRule(sample_rate=1, name=pattern), expected_to_match)
        for name, pattern, expected_to_match in [
            ("test.span", SamplingRule.NO_RULE, True),
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
def test_sampling_rule_matches_name(span, rule, span_expected_to_match_rule):
    assert rule.matches(span) is span_expected_to_match_rule, "{} -> {} -> {}".format(
        rule, span, span_expected_to_match_rule
    )


@pytest.mark.parametrize(
    "span,rule,span_expected_to_match_rule",
    [
        (create_span(service=service), SamplingRule(sample_rate=1, service=pattern), expected_to_match)
        for service, pattern, expected_to_match in [
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
def test_sampling_rule_matches_service(span, rule, span_expected_to_match_rule):
    assert rule.matches(span) is span_expected_to_match_rule, "{} -> {} -> {}".format(
        rule, span, span_expected_to_match_rule
    )


@pytest.mark.parametrize(
    "span,rule,span_expected_to_match_rule",
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
def test_sampling_rule_matches(span, rule, span_expected_to_match_rule):
    assert rule.matches(span) is span_expected_to_match_rule, "{} -> {} -> {}".format(
        rule, span, span_expected_to_match_rule
    )


def test_sampling_rule_matches_exception():
    def pattern(prop):
        raise Exception("an error occurred")

    rule = SamplingRule(sample_rate=1.0, name=pattern)
    span = create_span(name="test.span")

    with mock.patch("ddtrace.sampler.log") as mock_log:
        assert (
            rule.matches(span) is False
        ), "SamplingRule should not match when its name pattern function throws an exception"
        mock_log.warning.assert_called_once_with(
            "%r pattern %r failed with %r",
            rule,
            pattern,
            "test.span",
            exc_info=True,
        )


@pytest.mark.subprocess(
    parametrize={"DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": ["true", "false"]},
)
def test_sampling_rule_sample():
    from ddtrace.sampler import SamplingRule
    from ddtrace.span import Span

    for sample_rate in [0.01, 0.1, 0.15, 0.25, 0.5, 0.75, 0.85, 0.9, 0.95, 0.991]:
        rule = SamplingRule(sample_rate=sample_rate)

        iterations = int(1e4 / sample_rate)
        sampled = sum(rule.sample(Span(name=str(i))) for i in range(iterations))

        deviation = abs(sampled - (iterations * sample_rate)) / (iterations * sample_rate)
        assert deviation < 0.05, (
            "Actual sample rate should be within 5 percent of set sample "
            "rate (actual: %f, set: %f, sampled count: %f)" % (deviation, sample_rate, sampled)
        )


def test_sampling_rule_sample_rate_1():
    rule = SamplingRule(sample_rate=1)

    iterations = int(1e4)
    assert all(
        rule.sample(Span(name=str(i))) for i in range(iterations)
    ), "SamplingRule with rate=1 should always keep samples"


def test_sampling_rule_sample_rate_0():
    rule = SamplingRule(sample_rate=0)

    iterations = int(1e4)
    assert (
        sum(rule.sample(Span(name=str(i))) for i in range(iterations)) == 0
    ), "SamplingRule with rate=0 should never keep samples"


def test_datadog_sampler_init():
    sampler = DatadogSampler()
    assert sampler.rules == [], "DatadogSampler initialized with no arguments should hold no rules"
    assert isinstance(
        sampler.limiter, RateLimiter
    ), "DatadogSampler initialized with no arguments should hold a RateLimiter"
    assert (
        sampler.limiter.rate_limit == DatadogSampler.DEFAULT_RATE_LIMIT
    ), "DatadogSampler initialized with no arguments should hold a RateLimiter with the default limit"

    rule = SamplingRule(sample_rate=1)
    sampler = DatadogSampler(rules=[rule])
    assert sampler.rules == [rule], "DatadogSampler initialized with a rule should hold that rule"
    assert (
        sampler.limiter.rate_limit == DatadogSampler.DEFAULT_RATE_LIMIT
    ), "DatadogSampler initialized with a rule should hold the default rate limit"

    sampler = DatadogSampler(rate_limit=10)
    assert sampler.limiter.rate_limit == 10, "DatadogSampler initialized with a rate limit should hold that rate limit"

    sampler = DatadogSampler(default_sample_rate=0.5)
    assert (
        sampler.limiter.rate_limit == DatadogSampler.DEFAULT_RATE_LIMIT
    ), "DatadogSampler initialized with default_sample_rate should hold the default rate limit"
    assert sampler.rules == [
        SamplingRule(sample_rate=0.5)
    ], "DatadogSampler initialized with default_sample_rate should hold a SamplingRule with that rate"

    with override_global_config(dict(_trace_sample_rate="0.5", _trace_rate_limit=10)):
        sampler = DatadogSampler()
        assert (
            sampler.limiter.rate_limit == 10
        ), "DatadogSampler initialized with no arguments and envvars set should hold a rate_limit from the envvar"
        assert sampler.rules == [
            SamplingRule(sample_rate=0.5)
        ], "DatadogSampler initialized with no arguments and envvars set should hold a sample_rate from the envvar"

    with override_global_config(dict(_trace_sample_rate="0")):
        sampler = DatadogSampler()
        assert (
            sampler.limiter.rate_limit == DatadogSampler.DEFAULT_RATE_LIMIT
        ), "DatadogSampler initialized with DD_TRACE_SAMPLE_RATE=0 envvar should hold the default rate limit"
        assert sampler.rules == [
            SamplingRule(sample_rate=0)
        ], "DatadogSampler initialized with DD_TRACE_SAMPLE_RATE=0 envvar should hold sample_rate=0"

    with override_global_config(dict(_trace_sample_rate="asdf")):
        with pytest.raises(ValueError):
            DatadogSampler()

    with override_global_config(dict(_trace_rate_limit="invalid-limit")):
        with pytest.raises(ValueError):
            DatadogSampler()

    invalid_rules = (None, True, False, object(), 1, Exception())
    for rule in invalid_rules:
        with pytest.raises(TypeError):
            DatadogSampler(rules=[rule])

    rule_1 = SamplingRule(sample_rate=1)
    rule_2 = SamplingRule(sample_rate=0.5, service="test")
    rule_3 = SamplingRule(sample_rate=0.25, name="flask.request")
    sampler = DatadogSampler(rules=[rule_1, rule_2, rule_3])
    assert sampler.rules == [
        rule_1,
        rule_2,
        rule_3,
    ], "DatadogSampler holds rules in the order they were given during initialization"

    sampler = DatadogSampler(rules=[rule_1, rule_2, rule_3], default_sample_rate=0.75)
    assert sampler.rules == [rule_1, rule_2, rule_3, SamplingRule(sample_rate=0.75)], (
        "When default_sample_rate is set, DatadogSampler holds a rule with the default rate at the end "
        "of its rule list"
    )


@mock.patch("ddtrace.sampler.RateSampler.sample")
def test_datadog_sampler_sample_no_rules(mock_sample, dummy_tracer):
    sampler = DatadogSampler()
    dummy_tracer.configure(sampler=sampler)

    mock_sample.return_value = True
    dummy_tracer.trace("test").finish()
    spans = dummy_tracer.pop()
    assert len(spans) == 1, "Span should have been written"
    assert_sampling_decision_tags(
        spans[0],
        agent=1.0,
        limit=None,
        rule=None,
        sampling_priority=AUTO_KEEP,
        trace_tag="-{}".format(SamplingMechanism.DEFAULT),
    )

    mock_sample.return_value = False
    dummy_tracer.trace("test").finish()
    spans = dummy_tracer.pop()
    assert len(spans) == 1, "Span should have been written"
    assert_sampling_decision_tags(
        spans[0], agent=1.0, limit=None, rule=None, sampling_priority=AUTO_REJECT, trace_tag=None
    )


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
    "sampler, sampling_priority, sampling_mechanism, rule, limit",
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
            USER_KEEP,
            SamplingMechanism.TRACE_SAMPLING_RULE,
            1.0,
            None,
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
            USER_KEEP,
            SamplingMechanism.TRACE_SAMPLING_RULE,
            0.5,
            None,
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
            USER_KEEP,
            SamplingMechanism.TRACE_SAMPLING_RULE,
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
            USER_REJECT,
            None,
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
            USER_REJECT,
            None,
            0.5,
            None,
        ),
        (
            DatadogSampler(
                default_sample_rate=0,
            ),
            USER_REJECT,
            None,
            0,
            None,
        ),
        (
            DatadogSampler(
                default_sample_rate=1.0,
                rate_limit=0,
            ),
            USER_REJECT,
            None,
            1.0,
            0.0,
        ),
        (
            DatadogSampler(
                rules=[
                    MatchSample(1),
                ],
            ),
            USER_KEEP,
            SamplingMechanism.TRACE_SAMPLING_RULE,
            1,
            None,
        ),
        (
            DatadogSampler(
                rules=[
                    MatchSample(1),
                ],
                rate_limit=0,
            ),
            USER_REJECT,
            None,
            1,
            None,
        ),
        (
            DatadogSampler(
                rules=[SamplingRule(sample_rate=0, name="span")],
            ),
            USER_REJECT,
            None,
            0,
            None,
        ),
    ],
)
def test_datadog_sampler_sample_rules(sampler, sampling_priority, sampling_mechanism, rule, limit, dummy_tracer):
    dummy_tracer.configure(sampler=sampler)
    dummy_tracer.trace("span").finish()
    spans = dummy_tracer.pop()

    assert len(spans) > 0, "A tracer using DatadogSampler should always emit its spans"
    span = spans[0]
    assert span.sampled, "A span emitted from a tracer using DatadogSampler should always have the 'sampled' flag set"
    trace_tag = "-%d" % sampling_mechanism if sampling_mechanism is not None else None
    assert_sampling_decision_tags(
        span, rule=rule, limit=limit, sampling_priority=sampling_priority, trace_tag=trace_tag
    )


def test_datadog_sampler_tracer_child(dummy_tracer):
    rule = SamplingRule(sample_rate=1.0)
    sampler = DatadogSampler(rules=[rule])
    dummy_tracer.configure(sampler=sampler)

    with dummy_tracer.trace("parent.span"):
        dummy_tracer.trace("child.span").finish()

    spans = dummy_tracer.pop()
    assert len(spans) == 2, "A tracer using a DatadogSampler should emit all of its spans"
    assert_sampling_decision_tags(
        spans[0],
        rule=1.0,
        limit=None,
        sampling_priority=USER_KEEP,
        trace_tag="-{}".format(SamplingMechanism.TRACE_SAMPLING_RULE),
    )
    assert_sampling_decision_tags(
        spans[1],
        agent=None,
        rule=None,
        limit=None,
        trace_tag="-{}".format(SamplingMechanism.TRACE_SAMPLING_RULE),
    )


def test_datadog_sampler_tracer_start_span(dummy_tracer):
    rule = SamplingRule(sample_rate=1.0)
    sampler = DatadogSampler(rules=[rule])
    dummy_tracer.configure(sampler=sampler)
    dummy_tracer.start_span("test.span").finish()
    spans = dummy_tracer.pop()
    assert len(spans) == 1, "A tracer using a DatadogSampler should emit all of its spans"
    assert_sampling_decision_tags(
        spans[0],
        rule=1.0,
        limit=None,
        sampling_priority=USER_KEEP,
        trace_tag="-{}".format(SamplingMechanism.TRACE_SAMPLING_RULE),
    )


@pytest.mark.parametrize("priority_sampler", [DatadogSampler(), RateByServiceSampler()])
def test_update_rate_by_service_sample_rates(priority_sampler):
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

    for given_rates in cases:
        priority_sampler.update_rate_by_service_sample_rates(given_rates)
        actual_rates = {}
        for k, v in iteritems(priority_sampler._by_service_samplers):
            actual_rates[k] = v.sample_rate
        assert given_rates == actual_rates, "sampler should store the rates it's given"
    # It's important to also test in reverse mode for we want to make sure key deletion
    # works as well as key insertion (and doing this both ways ensures we trigger both cases)
    cases.reverse()
    for given_rates in cases:
        priority_sampler.update_rate_by_service_sample_rates(given_rates)
        actual_rates = {}
        for k, v in iteritems(priority_sampler._by_service_samplers):
            actual_rates[k] = v.sample_rate
        assert given_rates == actual_rates, "sampler should store the rates it's given"


@pytest.fixture()
def context():
    yield Context()


@pytest.mark.parametrize(
    "sampling_mechanism,sampled,expected",
    [
        (SamplingMechanism.AGENT_RATE, True, "-1"),
        (SamplingMechanism.TRACE_SAMPLING_RULE, True, "-3"),
        (SamplingMechanism.DEFAULT, True, "-0"),
        (SamplingMechanism.MANUAL, True, "-4"),
        (SamplingMechanism.DEFAULT, True, "-0"),
        (SamplingMechanism.DEFAULT, False, None),
    ],
)
def test_trace_tag(context, sampling_mechanism, sampled, expected):

    update_sampling_decision(context, sampling_mechanism, sampled)
    if sampled:
        assert context._meta["_dd.p.dm"] == expected
    else:
        assert "_dd.p.dm" not in context._meta
