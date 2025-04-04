import json

import pytest

from tests.integration.utils import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
RESOURCE = "mycoolre$ource"  # codespell:ignore
TAGS = {"tag1": "mycooltag"}


@pytest.mark.snapshot()
@pytest.mark.subprocess()
def test_sampling_with_defaults():
    from ddtrace.trace import tracer

    with tracer.trace("trace1"):
        tracer.trace("child").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={"DD_TRACE_RATE_LIMIT": "3"},
    err=b"DD_TRACE_RATE_LIMIT is set to 3 and DD_TRACE_SAMPLING_RULES is not set. "
    b"Tracer rate limiting is only applied to spans that match tracer sampling rules. "
    b"All other spans will be rate limited by the Datadog Agent via DD_APM_MAX_TPS.\n",
)
def test_sampling_with_rate_limit_3():
    from ddtrace.trace import tracer

    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "resource": RESOURCE}])})
def test_extended_sampling_resource():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import RESOURCE

    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send", resource="something else").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "tags": TAGS}])})
def test_extended_sampling_tags():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import TAGS

    tracer._tags = TAGS
    tracer.trace("should_not_send").finish()
    tracer._tags = {"banana": "True"}
    tracer.trace("should_send").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "tags": {"tag1": "mycool*"}}])})
def test_extended_sampling_tags_glob():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import TAGS

    tracer._tags = TAGS
    tracer.trace("should_not_send").finish()
    tracer._tags = {"tag1": "mcooltag"}
    tracer.trace("should_send").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "resource": "BANANA"}])})
def test_extended_sampling_tags_glob_insensitive_case_match():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import TAGS

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource="bananA").finish()
    tracer.trace("should_send2", resource="ban").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "resource": RESOURCE, "tags": TAGS}])}
)
def test_extended_sampling_tags_and_resource():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import RESOURCE
    from tests.integration.test_sampling import TAGS

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", resource=RESOURCE).finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "resource": RESOURCE, "tags": {"test": None}}])}
)
def test_extended_sampling_w_None_meta():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import RESOURCE

    tracer._tags = {"test": None}
    tracer.trace("should_not_send", resource=RESOURCE).finish()

    tracer._tags = {"test": "None"}
    tracer.trace("should_not_send2", resource=RESOURCE).finish()

    tracer.trace("should_send1", resource="banana").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "resource": RESOURCE, "tags": {"test": 123}}])}
)
def test_extended_sampling_w_metrics(tracer):
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import RESOURCE

    tracer._tags = {"test": 123}
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    # "123" actually gets set in _meta, not _metrics, but good to test that
    # both _meta and _metrics are checked by the rule
    tracer._tags = {"test": "123"}
    tracer.trace("should_not_send2", resource=RESOURCE).finish()

    tracer._tags = {"test": "1234"}
    tracer.trace("should_send1", resource="banana").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={
        "DD_TRACE_SAMPLING_RULES": json.dumps(
            [
                {"sample_rate": 0, "service": "webserv?r.non-matching", "name": "web.req*"},
                {"sample_rate": 0, "service": "webserv?r", "name": "web.req*.non-matching"},
                {"sample_rate": 1, "service": "webserv?r", "name": "web.req*"},
            ]
        )
    }
)
def test_extended_sampling_glob_multi_rule():
    from ddtrace.trace import tracer

    tracer._tags = {"test": "tag"}
    tracer.trace(name="web.reqUEst", service="wEbServer").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "resource": "mycoolre$ou*", "tags": TAGS}])}
)
def test_extended_sampling_tags_and_resource_glob():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import RESOURCE
    from tests.integration.test_sampling import TAGS

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", resource=RESOURCE).finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "service": "mycoolser????", "tags": TAGS}])}
)
def test_extended_sampling_tags_and_service_glob():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import TAGS

    tracer._tags = TAGS
    tracer.trace("should_not_send", service="mycoolservice").finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", service="mycoolservice").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "name": "mycoolna*", "tags": TAGS}])}
)
def test_extended_sampling_tags_and_name_glob():
    from ddtrace.trace import tracer
    from tests.integration.test_sampling import TAGS

    tracer._tags = TAGS
    tracer.trace(name="mycoolname").finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace(name="mycoolname").finish()


@pytest.mark.snapshot()
@pytest.mark.subprocess(env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "tags": {"tag": "2*"}}])})
def test_extended_sampling_float_special_case_do_not_match():
    """A float with a non-zero decimal and a tag with a non-* pattern
    # should not match the rule, and should therefore be kept
    """
    from ddtrace.trace import tracer

    with tracer.trace(name="should_send") as span:
        span.set_tag("tag", 20.1)


@pytest.mark.snapshot()
@pytest.mark.subprocess(env={"DD_TRACE_SAMPLING_RULES": json.dumps([{"sample_rate": 0, "tags": {"tag": "*"}}])})
def test_extended_sampling_float_special_case_match_star():
    """A float with a non-zero decimal and a tag with a * pattern
    # should match the rule, and should therefore should be dropped
    """
    from ddtrace.trace import tracer

    with tracer.trace(name="should_send") as span:
        span.set_tag("tag", 20.1)


@pytest.mark.subprocess()
def test_rate_limiter_on_spans(tracer):
    """
    Ensure that the rate limiter is applied to spans
    """
    from ddtrace._trace.sampler import DatadogSampler
    from ddtrace._trace.sampling_rule import SamplingRule
    from ddtrace.trace import tracer

    # Rate limit is only applied if a sample rate or trace sample rule is set
    tracer._sampler = DatadogSampler(rules=[SamplingRule(1.0)], rate_limit=10)
    spans = []
    # Generate 10 spans with the start and finish time in same second
    for x in range(10):
        start_time = x / 10
        span = tracer.trace(name=f"span {start_time}")
        span.start = start_time
        span.finish(1 - start_time)
        spans.append(span)
    # Generate 11th span in the same second
    dropped_span = tracer.trace(name=f"span {start_time}")
    dropped_span.start = 0.8
    dropped_span.finish(0.9)
    # Spans are sampled on flush
    tracer.flush()
    # Since the rate limiter is set to 10, first ten spans should be kept
    for span in spans:
        assert span.context.sampling_priority > 0
    # 11th span should be dropped
    assert dropped_span.context.sampling_priority < 0


@pytest.mark.subprocess()
def test_rate_limiter_on_long_running_spans(tracer):
    """
    Ensure that the rate limiter is applied on increasing time intervals
    """
    import mock

    from ddtrace._trace.sampler import DatadogSampler
    from ddtrace.trace import tracer

    tracer._sampler = DatadogSampler(rate_limit=5)

    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=1617333414):
        span_m30 = tracer.trace(name="march 30")
        span_m30.start = 1622347257  # Mar 30 2021
        span_m30.finish(1617333414)  # April 2 2021

        span_m29 = tracer.trace(name="march 29")
        span_m29.start = 1616999414  # Mar 29 2021
        span_m29.finish(1617333414)  # April 2 2021

    assert span_m29.context.sampling_priority > 0
    assert span_m30.context.sampling_priority > 0
