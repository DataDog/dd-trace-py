import pytest

from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.sampler import DatadogSampler
from ddtrace.sampler import RateSampler
from ddtrace.sampler import SamplingRule
from tests import snapshot

from .test_integration import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@snapshot(include_tracer=True)
def test_single_trace_single_span(tracer):
    s = tracer.trace("operation", service="my-svc")
    s.set_tag("k", "v")
    # numeric tag
    s.set_tag("num", 1234)
    s.set_metric("float_metric", 12.34)
    s.set_metric("int_metric", 4321)
    s.finish()
    tracer.shutdown()


@snapshot(include_tracer=True)
def test_multiple_traces(tracer):
    with tracer.trace("operation1", service="my-svc") as s:
        s.set_tag("k", "v")
        s.set_tag("num", 1234)
        s.set_metric("float_metric", 12.34)
        s.set_metric("int_metric", 4321)
        tracer.trace("child").finish()

    with tracer.trace("operation2", service="my-svc") as s:
        s.set_tag("k", "v")
        s.set_tag("num", 1234)
        s.set_metric("float_metric", 12.34)
        s.set_metric("int_metric", 4321)
        tracer.trace("child").finish()
    tracer.shutdown()


@snapshot(include_tracer=True)
def test_filters(tracer):
    class FilterMutate(object):
        def __init__(self, key, value):
            self.key = key
            self.value = value

        def process_trace(self, trace):
            for s in trace:
                s.set_tag(self.key, self.value)
            return trace

    tracer.configure(
        settings={
            "FILTERS": [FilterMutate("boop", "beep")],
        },
        writer=tracer.writer,
    )

    with tracer.trace("root"):
        with tracer.trace("child"):
            pass
    tracer.shutdown()


@snapshot(include_tracer=True)
def test_sampling(tracer):
    with tracer.trace("trace1"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=1.0)
    tracer.configure(sampler=sampler, writer=tracer.writer)
    with tracer.trace("trace2"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=0.000001)
    tracer.configure(sampler=sampler, writer=tracer.writer)
    with tracer.trace("trace3"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(1.0)])
    tracer.configure(sampler=sampler, writer=tracer.writer)
    with tracer.trace("trace4"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(0)])
    tracer.configure(sampler=sampler, writer=tracer.writer)
    with tracer.trace("trace5"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=1)
    tracer.configure(sampler=sampler, writer=tracer.writer)
    with tracer.trace("trace6"):
        with tracer.trace("child") as span:
            span.set_tag(MANUAL_DROP_KEY)

    sampler = DatadogSampler(default_sample_rate=1)
    tracer.configure(sampler=sampler, writer=tracer.writer)
    with tracer.trace("trace7"):
        with tracer.trace("child") as span:
            span.set_tag(MANUAL_KEEP_KEY)

    sampler = RateSampler(0.0000000001)
    tracer.configure(sampler=sampler, writer=tracer.writer)
    # This trace should not appear in the snapshot
    with tracer.trace("trace8"):
        with tracer.trace("child"):
            pass

    tracer.shutdown()
