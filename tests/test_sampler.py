from __future__ import division
import re
import unittest

import pytest

from ddtrace.compat import iteritems
from ddtrace.constants import SAMPLING_PRIORITY_KEY, SAMPLE_RATE_METRIC_KEY
from ddtrace.sampler import SamplingRule
from ddtrace.sampler import RateSampler, AllSampler, RateByServiceSampler
from ddtrace.span import Span

from .test_tracer import get_dummy_tracer


def create_span(name='test.span', meta=None, *args, **kwargs):
    tracer = get_dummy_tracer()
    span = Span(tracer=tracer, name=name, *args, **kwargs)
    if meta:
        span.set_tags(meta)
    return span


class RateSamplerTest(unittest.TestCase):

    def test_sample_rate_deviation(self):
        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = get_dummy_tracer()
            writer = tracer.writer

            tracer.sampler = RateSampler(sample_rate)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = writer.pop()

            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) == sample_rate

            # Less than 5% deviation when 'enough' iterations (arbitrary, just check if it converges)
            deviation = abs(len(samples) - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.05, 'Deviation too high %f with sample_rate %f' % (deviation, sample_rate)

    def test_deterministic_behavior(self):
        """ Test that for a given trace ID, the result is always the same """
        tracer = get_dummy_tracer()
        writer = tracer.writer

        tracer.sampler = RateSampler(0.5)

        for i in range(10):
            span = tracer.trace(i)
            span.finish()

            samples = writer.pop()
            assert len(samples) <= 1, 'there should be 0 or 1 spans'
            sampled = (1 == len(samples))
            for j in range(10):
                other_span = Span(tracer, i, trace_id=span.trace_id)
                assert (
                    sampled == tracer.sampler.sample(other_span)
                ), 'sampling should give the same result for a given trace_id'


class RateByServiceSamplerTest(unittest.TestCase):
    def test_default_key(self):
        assert (
            'service:,env:' == RateByServiceSampler._default_key
        ), 'default key should correspond to no service and no env'

    def test_key(self):
        assert RateByServiceSampler._default_key == RateByServiceSampler._key()
        assert 'service:mcnulty,env:' == RateByServiceSampler._key(service='mcnulty')
        assert 'service:,env:test' == RateByServiceSampler._key(env='test')
        assert 'service:mcnulty,env:test' == RateByServiceSampler._key(service='mcnulty', env='test')
        assert 'service:mcnulty,env:test' == RateByServiceSampler._key('mcnulty', 'test')

    def test_sample_rate_deviation(self):
        for sample_rate in [0.1, 0.25, 0.5, 1]:
            tracer = get_dummy_tracer()
            writer = tracer.writer
            tracer.configure(sampler=AllSampler())
            # We need to set the writer because tracer.configure overrides it,
            # indeed, as we enable priority sampling, we must ensure the writer
            # is priority sampling aware and pass it a reference on the
            # priority sampler to send the feedback it gets from the agent
            assert writer != tracer.writer, 'writer should have been updated by configure'
            tracer.writer = writer
            tracer.priority_sampler.set_sample_rate(sample_rate)

            iterations = int(1e4 / sample_rate)

            for i in range(iterations):
                span = tracer.trace(i)
                span.finish()

            samples = writer.pop()
            samples_with_high_priority = 0
            for sample in samples:
                if sample.get_metric(SAMPLING_PRIORITY_KEY) is not None:
                    if sample.get_metric(SAMPLING_PRIORITY_KEY) > 0:
                        samples_with_high_priority += 1
                else:
                    assert (
                        0 == sample.get_metric(SAMPLING_PRIORITY_KEY)
                    ), 'when priority sampling is on, priority should be 0 when trace is to be dropped'

            # We must have at least 1 sample, check that it has its sample rate properly assigned
            assert samples[0].get_metric(SAMPLE_RATE_METRIC_KEY) is None

            # Less than 5% deviation when 'enough' iterations (arbitrary, just check if it converges)
            deviation = abs(samples_with_high_priority - (iterations * sample_rate)) / (iterations * sample_rate)
            assert deviation < 0.05, 'Deviation too high %f with sample_rate %f' % (deviation, sample_rate)

    def test_set_sample_rate_by_service(self):
        cases = [
            {
                'service:,env:': 1,
            },
            {
                'service:,env:': 1,
                'service:mcnulty,env:dev': 0.33,
                'service:postgres,env:dev': 0.7,
            },
            {
                'service:,env:': 1,
                'service:mcnulty,env:dev': 0.25,
                'service:postgres,env:dev': 0.5,
                'service:redis,env:prod': 0.75,
            },
        ]

        tracer = get_dummy_tracer()
        tracer.configure(sampler=AllSampler())
        priority_sampler = tracer.priority_sampler
        for case in cases:
            priority_sampler.set_sample_rate_by_service(case)
            rates = {}
            for k, v in iteritems(priority_sampler._by_service_samplers):
                rates[k] = v.sample_rate
            assert case == rates, '%s != %s' % (case, rates)
        # It's important to also test in reverse mode for we want to make sure key deletion
        # works as well as key insertion (and doing this both ways ensures we trigger both cases)
        cases.reverse()
        for case in cases:
            priority_sampler.set_sample_rate_by_service(case)
            rates = {}
            for k, v in iteritems(priority_sampler._by_service_samplers):
                rates[k] = v.sample_rate
            assert case == rates, '%s != %s' % (case, rates)


@pytest.mark.parametrize(
    'sample_rate,allowed',
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
    ] + [
        # Try a bunch of decimal values between 0 and 1
        (1 / i, True) for i in range(1, 50)
    ] + [
        # Try a bunch of decimal values less than 0
        (-(1 / i), False) for i in range(1, 50)
    ] + [
        # Try a bunch of decimal values greater than 1
        (1 + (1 / i), False) for i in range(1, 50)
    ]
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
    assert rule.resource == SamplingRule.NO_RULE
    assert rule.tags == SamplingRule.NO_RULE


def test_sampling_rule_init_tags():
    # Not supplied
    SamplingRule(sample_rate=1.0)

    # As a dict
    SamplingRule(sample_rate=1.0, tags=dict())

    # Non-dict
    for value in [
        None,
        'value',
        re.compile('value'),
        lambda tags: True,
    ]:
        with pytest.raises(TypeError):
            SamplingRule(sample_rate=1.0, tags=value)


def test_sampling_rule_init():
    name_regex = re.compile(r'\.request$')

    def resource_check(resource):
        return 'healthcheck' in resource

    tag_regex = re.compile(r'^(GET)|(POST)$')

    def tag_check(tag):
        return 'healthcheck' in tag

    rule = SamplingRule(
        sample_rate=0.0,
        # Value
        service='my-service',
        # Regex
        name=name_regex,
        # Callable
        resource=resource_check,
        tags={
            'http.status': 202,
            'http.method': tag_regex,
            'http.url': tag_check,
        },
    )

    assert rule.sample_rate == 0.0
    assert rule.service == 'my-service'
    assert rule.name == name_regex
    assert rule.resource == resource_check
    assert rule.tags == {
        'http.status': 202,
        'http.method': tag_regex,
        'http.url': tag_check,
    }


@pytest.mark.parametrize(
    'span,rule,expected',
    [
        # DEV: Use sample_rate=1 to ensure SamplingRule._sample always returns True
        (create_span(name=name), SamplingRule(
            sample_rate=1, name=pattern), expected)
        for name, pattern, expected in [
            ('test.span', SamplingRule.NO_RULE, True),
            # DEV: `span.name` cannot be `None`
            ('test.span', None, False),
            ('test.span', 'test.span', True),
            ('test.span', 'test_span', False),
            ('test.span', re.compile(r'^test\.span$'), True),
            ('test_span', re.compile(r'^test.span$'), True),
            ('test.span', re.compile(r'^test_span$'), False),
            ('test.span', re.compile(r'test'), True),
            ('test.span', re.compile(r'test\.span|another\.span'), True),
            ('another.span', re.compile(r'test\.span|another\.span'), True),
            ('test.span', lambda name: 'span' in name, True),
            ('test.span', lambda name: 'span' not in name, False),
            ('test.span', lambda name: 1/0, False),
        ]
    ]
)
def test_sampling_rule_should_sample_name(span, rule, expected):
    assert rule.should_sample(span) is expected, '{} -> {} -> {}'.format(rule, span, expected)


@pytest.mark.parametrize(
    'span,rule,expected',
    [
        # DEV: Use sample_rate=1 to ensure SamplingRule._sample always returns True
        (create_span(service=service), SamplingRule(sample_rate=1, service=pattern), expected)
        for service, pattern, expected in [
                ('my-service', SamplingRule.NO_RULE, True),
                ('my-service', None, False),
                (None, None, True),
                (None, 'my-service', False),
                (None, re.compile(r'my-service'), False),
                (None, lambda service: 'service' in service, False),
                ('my-service', 'my-service', True),
                ('my-service', 'my_service', False),
                ('my-service', re.compile(r'^my-'), True),
                ('my_service', re.compile(r'^my[_-]'), True),
                ('my-service', re.compile(r'^my_'), False),
                ('my-service', re.compile(r'my-service'), True),
                ('my-service', re.compile(r'my'), True),
                ('my-service', re.compile(r'my-service|another-service'), True),
                ('another-service', re.compile(r'my-service|another-service'), True),
                ('my-service', lambda service: 'service' in service, True),
                ('my-service', lambda service: 'service' not in service, False),
                ('my-service', lambda service: 1/0, False),
        ]
    ]
)
def test_sampling_rule_should_sample_service(span, rule, expected):
    assert rule.should_sample(span) is expected, '{} -> {} -> {}'.format(rule, span, expected)


@pytest.mark.parametrize(
    'span,rule,expected',
    [
        # DEV: Use sample_rate=1 to ensure SamplingRule._sample always returns True
        (create_span(resource=resource), SamplingRule(sample_rate=1, resource=pattern), expected)
        for resource, pattern, expected in [
                ('GET /healthcheck', SamplingRule.NO_RULE, True),
                ('GET /healthcheck', None, False),
                # False because `resource=None` inherits from the span name
                (None, None, False),
                # True because `resource=None` inherits from the span name
                (None, re.compile(r'(test|another)[._]span'), True),
                (None, 'GET /healthcheck', False),
                (None, re.compile(r'GET /healthcheck'), False),
                (None, lambda resource: 'healthcheck' in resource, False),
                ('GET /healthcheck', 'GET /healthcheck', True),
                ('GET /healthcheck', 'GET /users', False),
                ('GET /healthcheck', re.compile(r'^GET'), True),
                ('POST /healthcheck', re.compile(r'^(GET|POST) /healthcheck'), True),
                ('GET /healthcheck', re.compile(r'^POST'), False),
                ('GET /healthcheck', re.compile(r'GET /healthcheck'), True),
                ('GET /healthcheck', re.compile(r'GET'), True),
                ('GET /healthcheck', re.compile(r'(GET /healthcheck)|(POST /users/create)'), True),
                ('POST /users/create', re.compile(r'(GET /healthcheck)|(POST /users/create)'), True),
                ('GET /healthcheck', lambda resource: 'healthcheck' in resource, True),
                ('GET /healthcheck', lambda resource: 'healthcheck' not in resource, False),
                ('GET /healthcheck', lambda resource: 1/0, False),
        ]
    ]
)
def test_sampling_rule_should_sample_resource(span, rule, expected):
    assert rule.should_sample(span) is expected, '{} -> {} -> {}'.format(rule, span, expected)


@pytest.mark.parametrize(
    'span,rule,expected',
    [
        # DEV: Use sample_rate=1 to ensure SamplingRule._sample always returns True
        (create_span(meta=meta), SamplingRule(sample_rate=1, tags=pattern), expected)
        for meta, pattern, expected in [
            (None, SamplingRule.NO_RULE, True),
            (None, dict(), True),
            (dict(key='value'), dict(key='value'), True),
            (dict(key='value'), dict(key='wrong'), False),
            (dict(a=1, b=2), dict(a='1', b='2'), True),
            (dict(a=1, b=2), dict(a=1, b=2), False),
            (dict(a=1, b=2), dict(a='2', b='1'), False),
            (dict(key='localhost:8126'), dict(key=re.compile(r'localhost:[0-9]+')), True),
            (dict(key='localhost:8126'), dict(key=re.compile(r'localhost:[0-9]{3}$')), False),
            (dict(key='localhost:8126'), dict(key=lambda tag: tag.endswith('8126')), True),
            (dict(key='localhost:8126'), dict(key=lambda tag: not tag.endswith('8126')), False),
            (dict(key='localhost:8126'), dict(key=lambda tag: 1/0), False),
            (
                dict(
                    string='value',
                    regex='ca6089f3519040ad802c5abb85d3a32d',
                    func='GET /healthcheck',
                ),
                dict(
                    string='value',
                    regex=re.compile(r'^[a-f0-9]{32}$'),
                    func=lambda tag: 'healthcheck' in tag,
                ),
                True,
            ),
            (
                dict(
                    string='value',
                    regex='ca6089f3519040ad802c5abb85d3a32d',
                    func='GET /healthcheck',
                ),
                dict(
                    string='value',
                    # This regex will not match
                    regex=re.compile(r'^[a-f0-9]{30}$'),
                    func=lambda tag: 'healthcheck' in tag,
                ),
                False,
            ),
            (
                dict(
                    string='value',
                    regex='ca6089f3519040ad802c5abb85d3a32d',
                    func='GET /healthcheck',
                ),
                dict(
                    # Mismatch on string comparision
                    string='another',
                    regex=re.compile(r'^[a-f0-9]{32}$'),
                    func=lambda tag: 'healthcheck' in tag,
                ),
                False,
            ),
            (
                dict(
                    string='value',
                    regex='ca6089f3519040ad802c5abb85d3a32d',
                    func='GET /healthcheck',
                ),
                dict(
                    string='value',
                    regex=re.compile(r'^[a-f0-9]{32}$'),
                    # Function returns False
                    func=lambda tag: 'healthcheck' not in tag,
                ),
                False,
            ),
        ]
    ]
)
def test_sampling_rule_should_sample_meta(span, rule, expected):
    assert rule.should_sample(span) is expected, '{} -> {} -> {}'.format(rule, span, expected)


@pytest.mark.parametrize(
    'span,rule,expected',
    [
        # All match
        (
            create_span(
                name='test.span',
                service='my-service',
                resource='GET /healthcheck',
                meta={'http.status': '200'},
            ),
            SamplingRule(
                sample_rate=1,
                name='test.span',
                service=re.compile(r'^my-'),
                resource=lambda resource: resource.endswith('/healthcheck'),
                tags={'http.status': '200'},
            ),
            True,
        ),

        # All match,  but sample rate of 0%
        (
            create_span(
                name='test.span',
                service='my-service',
                resource='GET /healthcheck',
                meta={'http.status': '200'},
            ),
            SamplingRule(
                sample_rate=0,
                name='test.span',
                service=re.compile(r'^my-'),
                resource=lambda resource: resource.endswith('/healthcheck'),
                tags={'http.status': '200'},
            ),
            False,
        ),

        # Name doesn't match
        (
            create_span(
                name='test.span',
                service='my-service',
                resource='GET /healthcheck',
                meta={'http.status': '200'},
            ),
            SamplingRule(
                sample_rate=1,
                name='test_span',
                service=re.compile(r'^my-'),
                resource=lambda resource: resource.endswith('/healthcheck'),
                tags={'http.status': '200'},
            ),
            False,
        ),

        # Service doesn't match
        (
            create_span(
                name='test.span',
                service='my-service',
                resource='GET /healthcheck',
                meta={'http.status': '200'},
            ),
            SamplingRule(
                sample_rate=1,
                name='test.span',
                service=re.compile(r'^service-'),
                resource=lambda resource: resource.endswith('/healthcheck'),
                tags={'http.status': '200'},
            ),
            False,
        ),

        # Resource doesn't match
        (
            create_span(
                name='test.span',
                service='my-service',
                resource='GET /healthcheck',
                meta={'http.status': '200'},
            ),
            SamplingRule(
                sample_rate=1,
                name='test.span',
                service=re.compile(r'^my-'),
                resource=lambda resource: resource.endswith('/users/create'),
                tags={'http.status': '200'},
            ),
            False,
        ),

        # Tags do not match
        (
            create_span(
                name='test.span',
                service='my-service',
                resource='GET /healthcheck',
                meta={'http.status': '200'},
            ),
            SamplingRule(
                sample_rate=1,
                name='test.span',
                service=re.compile(r'^my-'),
                resource=lambda resource: resource.endswith('/healthcheck'),
                tags={'http.status': '202'},
            ),
            False,
        ),
    ],
)
def test_sampling_rule_should_sample(span, rule, expected):
    assert rule.should_sample(span) is expected, '{} -> {} -> {}'.format(rule, span, expected)


@pytest.mark.parametrize('sample_rate', [0.01, 0.1, 0.15, 0.25, 0.5, 0.75, 0.85, 0.9, 0.95, 0.991])
def test_sampling_rule_should_sample_sample_rate(sample_rate):
    tracer = get_dummy_tracer()
    rule = SamplingRule(sample_rate=sample_rate)

    iterations = int(1e4 / sample_rate)
    sampled = sum(
        rule.should_sample(Span(tracer=tracer, name=i))
        for i in range(iterations)
    )

    # Less than 5% deviation when 'enough' iterations (arbitrary, just check if it converges)
    deviation = abs(sampled - (iterations * sample_rate)) / (iterations * sample_rate)
    assert deviation < 0.05, (
        'Deviation {!r} too high with sample_rate {!r} for {} sampled'.format(deviation, sample_rate, sampled)
    )


def test_sampling_rule_should_sample_sample_rate_1():
    tracer = get_dummy_tracer()
    rule = SamplingRule(sample_rate=1)

    iterations = int(1e4)
    assert all(
        rule.should_sample(Span(tracer=tracer, name=i))
        for i in range(iterations)
    )


def test_sampling_rule_should_sample_sample_rate_0():
    tracer = get_dummy_tracer()
    rule = SamplingRule(sample_rate=0)

    iterations = int(1e4)
    assert sum(
        rule.should_sample(Span(tracer=tracer, name=i))
        for i in range(iterations)
    ) == 0
