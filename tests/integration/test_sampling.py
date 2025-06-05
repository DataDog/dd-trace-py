import pytest

from ddtrace._trace.sampler import DatadogSampler
from ddtrace._trace.sampler import RateSampler
from ddtrace._trace.sampler import SamplingRule
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.internal.writer import AgentWriter
from tests.integration.utils import AGENT_VERSION
from tests.utils import snapshot


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
RESOURCE = "mycoolre$ource"  # codespell:ignore
TAGS = {"tag1": "mycooltag"}


def snapshot_parametrized_with_writers(f):
    def _patch(writer, tracer):
        old_sampler = tracer._sampler
        old_writer = tracer._writer
        old_tags = tracer._tags
        if writer == "sync":
            writer = AgentWriter(
                tracer.agent_trace_url,
                sync_mode=True,
            )
            # NB Need to copy the headers, which contain the snapshot token, to associate
            # snapshots with this test case
            writer._headers = tracer._writer._headers
        else:
            writer = tracer._writer
        try:
            return f(writer, tracer)
        finally:
            tracer.flush()
            # Reset tracer configurations to avoid leaking state between tests
            tracer._configure(sampler=old_sampler, writer=old_writer)
            tracer._tags = old_tags

    wrapped = snapshot(include_tracer=True, token_override=f.__name__)(_patch)
    return pytest.mark.parametrize(
        "writer",
        ("default", "sync"),
    )(wrapped)


@snapshot_parametrized_with_writers
def test_sampling_with_defaults(writer, tracer):
    with tracer.trace("trace1"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1.0)
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace2"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_tiny(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=0.000001)
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace3"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1_and_rule_1(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(1.0)])
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace4"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1_and_rule_0(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(0)])
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1_and_manual_drop(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1)
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace6"):
        with tracer.trace("child") as span:
            span.set_tag(MANUAL_DROP_KEY)


def test_supported_sampling_mechanism():
    """
    validate_sampling_decision should not give errors for supported sampling mechanisms
    """
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.sampling import SamplingMechanism
    from ddtrace.internal.sampling import validate_sampling_decision

    # This list can grow over time so we should test all of them
    supported_mechanisms = {
        name: getattr(SamplingMechanism, name) for name in dir(SamplingMechanism) if not name.startswith("_")
    }

    # The mechanisms we support should NOT return a decoding error
    for mechanism, value in supported_mechanisms.items():
        sampling_decision_validation = None
        meta = {}
        sampling_dm_value = "-" + str(value)
        meta = {SAMPLING_DECISION_TRACE_TAG_KEY: sampling_dm_value}
        sampling_decision_validation = validate_sampling_decision(meta)
        decoding_error_result = {"_dd.propagation_error": "decoding_error"}
        assert sampling_decision_validation != decoding_error_result, f"{mechanism} returned {decoding_error_result}"


def test_unsupported_sampling_mechanism():
    """
    Unsupported sampling mechanisms actually return a decoding error in validate_sampling_decision
    """
    from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
    from ddtrace.internal.sampling import validate_sampling_decision

    meta = {SAMPLING_DECISION_TRACE_TAG_KEY: "-999999999999"}
    sampling_decision_validation = validate_sampling_decision(meta)
    decoding_error_result = {"_dd.propagation_error": "decoding_error"}
    assert (
        sampling_decision_validation == decoding_error_result
    ), f"Instead of getting {decoding_error_result}, received {sampling_decision_validation}"


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1_and_manual_keep(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1)
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace7"):
        with tracer.trace("child") as span:
            span.set_tag(MANUAL_KEEP_KEY)


@snapshot_parametrized_with_writers
def test_sampling_with_rate_sampler_with_tiny_rate(writer, tracer):
    sampler = RateSampler(0.0000000001)
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace8"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_sample_rate_1_and_rate_limit_0(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1, rate_limit=0)
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_sample_rate_1_and_rate_limit_3_and_rule_0(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(0)], rate_limit=3)
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_rate_limit_3(writer, tracer):
    sampler = DatadogSampler(rate_limit=3)
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_resource(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, resource=RESOURCE)])
    tracer._configure(sampler=sampler, writer=writer)
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send", resource="something else").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS)])
    tracer._configure(sampler=sampler, writer=writer)
    tracer._tags = TAGS
    tracer.trace("should_not_send").finish()
    tracer._tags = {"banana": "True"}
    tracer.trace("should_send").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_glob(writer, tracer):
    rule_tags = TAGS.copy()
    tag_key = list(rule_tags.keys())[0]
    tag_value = rule_tags[tag_key]
    rule_tags[tag_key] = tag_value[:2] + "*"

    sampler = DatadogSampler(rules=[SamplingRule(0, tags=rule_tags)])
    assert sampler.rules[0].tags == {tag_key: "my*"}
    tracer._configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send").finish()
    tracer._tags = {tag_key: "mcooltag"}
    tracer.trace("should_send").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_glob_insensitive_case_match(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, resource="BANANA")])
    tracer._configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource="bananA").finish()
    tracer.trace("should_send2", resource="ban").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_and_resource(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS, resource=RESOURCE)])
    tracer._configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", resource=RESOURCE).finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_w_None_meta(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags={"test": None}, resource=RESOURCE)])
    tracer._configure(sampler=sampler, writer=writer)

    tracer._tags = {"test": None}
    tracer.trace("should_not_send", resource=RESOURCE).finish()

    tracer._tags = {"test": "None"}
    tracer.trace("should_not_send2", resource=RESOURCE).finish()

    tracer.trace("should_send1", resource="banana").finish()


@snapshot()
def test_extended_sampling_w_metrics(tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags={"test": 123}, resource=RESOURCE)])
    tracer._configure(sampler=sampler)

    tracer._tags = {"test": 123}
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    # "123" actually gets set in _meta, not _metrics, but good to test that
    # both _meta and _metrics are checked by the rule
    tracer._tags = {"test": "123"}
    tracer.trace("should_not_send2", resource=RESOURCE).finish()

    tracer._tags = {"test": "1234"}
    tracer.trace("should_send1", resource="banana").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_glob_multi_rule(writer, tracer):
    sampler = DatadogSampler(
        rules=[
            SamplingRule(0, service="webserv?r.non-matching", name="web.req*"),
            SamplingRule(0, service="webserv?r", name="web.req*.non-matching"),
            SamplingRule(1, service="webserv?r", name="web.req*"),
        ]
    )
    tracer._configure(sampler=sampler, writer=writer)

    tracer._tags = {"test": "tag"}
    tracer.trace(name="web.reqUEst", service="wEbServer").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_and_resource_glob(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS, resource="mycoolre$ou*")])
    tracer._configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", resource=RESOURCE).finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_and_service_glob(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS, service="mycoolser????")])
    tracer._configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send", service="mycoolservice").finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", service="mycoolservice").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_and_name_glob(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS, name="mycoolna*")])
    tracer._configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace(name="mycoolname").finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace(name="mycoolname").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_float_special_case_do_not_match(writer, tracer):
    """A float with a non-zero decimal and a tag with a non-* pattern
    # should not match the rule, and should therefore be kept
    """
    sampler = DatadogSampler(rules=[SamplingRule(0, tags={"tag": "2*"})])
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace(name="should_send") as span:
        span.set_tag("tag", 20.1)


@snapshot_parametrized_with_writers
def test_extended_sampling_float_special_case_match_star(writer, tracer):
    """A float with a non-zero decimal and a tag with a * pattern
    # should match the rule, and should therefore should be dropped
    """
    sampler = DatadogSampler(rules=[SamplingRule(0, tags={"tag": "*"})])
    tracer._configure(sampler=sampler, writer=writer)
    with tracer.trace(name="should_send") as span:
        span.set_tag("tag", 20.1)


@pytest.mark.subprocess()
def test_rate_limiter_on_spans(tracer):
    """
    Ensure that the rate limiter is applied to spans
    """
    from ddtrace._trace.sampler import DatadogSampler
    from ddtrace.trace import tracer

    # Rate limit is only applied if a sample rate or trace sample rule is set
    tracer._configure(sampler=DatadogSampler(default_sample_rate=1, rate_limit=10))
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

    tracer._configure(sampler=DatadogSampler(rate_limit=5))

    with mock.patch("ddtrace.internal.rate_limiter.time.monotonic_ns", return_value=1617333414):
        span_m30 = tracer.trace(name="march 30")
        span_m30.start = 1622347257  # Mar 30 2021
        span_m30.finish(1617333414)  # April 2 2021

        span_m29 = tracer.trace(name="march 29")
        span_m29.start = 1616999414  # Mar 29 2021
        span_m29.finish(1617333414)  # April 2 2021

    assert span_m29.context.sampling_priority > 0
    assert span_m30.context.sampling_priority > 0
