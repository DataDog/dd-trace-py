import pytest

from ddtrace import config
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.internal.writer import AgentWriter
from ddtrace.sampler import DatadogSampler
from ddtrace.sampler import RateSampler
from ddtrace.sampler import SamplingRule
from tests.utils import snapshot

from .test_integration import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")
RESOURCE = "mycoolre$ource"
TAGS = {"tag1": "mycooltag"}


def snapshot_parametrized_with_writers(f):
    def _patch(writer, tracer):
        if writer == "sync":
            writer = AgentWriter(
                tracer.agent_trace_url,
                priority_sampling=config._priority_sampling,
                sync_mode=True,
            )
            # NB Need to copy the headers, which contain the snapshot token, to associate
            # snapshots with this test case
            writer._headers = tracer._writer._headers
        else:
            writer = tracer._writer
        tracer.configure(writer=writer)
        try:
            return f(writer, tracer)
        finally:
            tracer.shutdown()

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
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace2"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_tiny(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=0.000001)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace3"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1_and_rule_1(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(1.0)])
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace4"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1_and_rule_0(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(0)])
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1_and_manual_drop(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace6"):
        with tracer.trace("child") as span:
            span.set_tag(MANUAL_DROP_KEY)


@snapshot_parametrized_with_writers
def test_sampling_with_default_sample_rate_1_and_manual_keep(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace7"):
        with tracer.trace("child") as span:
            span.set_tag(MANUAL_KEEP_KEY)


@snapshot_parametrized_with_writers
def test_sampling_with_rate_sampler_with_tiny_rate(writer, tracer):
    sampler = RateSampler(0.0000000001)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace8"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_sample_rate_1_and_rate_limit_0(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1, rate_limit=0)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_sample_rate_1_and_rate_limit_3_and_rule_0(writer, tracer):
    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(0)], rate_limit=3)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_sampling_with_rate_limit_3(writer, tracer):
    sampler = DatadogSampler(rate_limit=3)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        tracer.trace("child").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_resource(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, resource=RESOURCE)])
    tracer.configure(sampler=sampler, writer=writer)
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send", resource="something else").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS)])
    tracer.configure(sampler=sampler, writer=writer)
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
    tracer.configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send").finish()
    tracer._tags = {tag_key: "mcooltag"}
    tracer.trace("should_send").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_glob_insensitive_case_match(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, resource="BANANA")])
    tracer.configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource="bananA").finish()
    tracer.trace("should_send2", resource="ban").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_and_resource(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS, resource=RESOURCE)])
    tracer.configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", resource=RESOURCE).finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_w_None_meta(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags={"test": None}, resource=RESOURCE)])
    tracer.configure(sampler=sampler, writer=writer)

    tracer._tags = {"test": None}
    tracer.trace("should_not_send", resource=RESOURCE).finish()

    tracer._tags = {"test": "None"}
    tracer.trace("should_not_send2", resource=RESOURCE).finish()

    tracer.trace("should_send1", resource="banana").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_w_metrics(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags={"test": 123}, resource=RESOURCE)])
    tracer.configure(sampler=sampler, writer=writer)

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
    tracer.configure(sampler=sampler, writer=writer)

    tracer._tags = {"test": "tag"}
    tracer.trace(name="web.reqUEst", service="wEbServer").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_and_resource_glob(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS, resource="mycoolre$ou*")])
    tracer.configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send", resource=RESOURCE).finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", resource=RESOURCE).finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_and_service_glob(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS, service="mycoolser????")])
    tracer.configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace("should_not_send", service="mycoolservice").finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace("should_send3", service="mycoolservice").finish()


@snapshot_parametrized_with_writers
def test_extended_sampling_tags_and_name_glob(writer, tracer):
    sampler = DatadogSampler(rules=[SamplingRule(0, tags=TAGS, name="mycoolna*")])
    tracer.configure(sampler=sampler, writer=writer)

    tracer._tags = TAGS
    tracer.trace(name="mycoolname").finish()
    tracer.trace("should_send2", resource="banana").finish()

    tracer._tags = {"banana": "True"}
    tracer.trace("should_send1").finish()
    tracer.trace(name="mycoolname").finish()
