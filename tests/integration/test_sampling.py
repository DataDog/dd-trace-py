import pytest

from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.internal.writer import AgentWriter
from ddtrace.sampler import DatadogSampler
from ddtrace.sampler import RateSampler
from ddtrace.sampler import SamplingRule
from tests.utils import snapshot


def snapshot_parametrized_with_writers(f):
    def _patch(writer, tracer):
        if writer == "sync":
            writer = AgentWriter(
                tracer.agent_trace_url,
                priority_sampler=tracer._priority_sampler,
                sync_mode=True,
            )
            # Need to copy the headers which contain the test token to associate
            # traces with this test case.
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
    # This trace should not appear in the snapshot
    with tracer.trace("trace8"):
        tracer.trace("child").finish()
