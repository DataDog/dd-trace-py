import pytest

from tests.integration.utils import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
RESOURCE = "mycoolre$ource"  # codespell:ignore
TAGS = {"tag1": "mycooltag"}


@pytest.mark.subprocess()
def test_rate_limiter_on_spans(tracer):
    """
    Ensure that the rate limiter is applied to spans
    """
    from ddtrace._trace.sampler import DatadogSampler
    from ddtrace.internal.sampling import SamplingRule
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
