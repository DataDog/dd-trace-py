import pytest

from ddtrace import Tracer
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.internal.writer import AgentWriter
from ddtrace.sampler import DatadogSampler
from ddtrace.sampler import RateSampler
from ddtrace.sampler import SamplingRule
from tests.utils import snapshot

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


@pytest.mark.parametrize(
    "writer",
    ("default", "sync"),
)
@snapshot(include_tracer=True)
def test_filters(writer, tracer):
    if writer == "sync":
        writer = AgentWriter(
            tracer.writer.agent_url,
            priority_sampler=tracer.priority_sampler,
            sync_mode=True,
        )
        # Need to copy the headers which contain the test token to associate
        # traces with this test case.
        writer._headers = tracer.writer._headers
    else:
        writer = tracer.writer

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
        writer=writer,
    )

    with tracer.trace("root"):
        with tracer.trace("child"):
            pass
    tracer.shutdown()


@pytest.mark.parametrize(
    "writer",
    ("default", "sync"),
)
@snapshot(include_tracer=True)
def test_sampling(writer, tracer):
    if writer == "sync":
        writer = AgentWriter(
            tracer.writer.agent_url,
            priority_sampler=tracer.priority_sampler,
            sync_mode=True,
        )
        # Need to copy the headers which contain the test token to associate
        # traces with this test case.
        writer._headers = tracer.writer._headers
    else:
        writer = tracer.writer

    tracer.configure(writer=writer)

    with tracer.trace("trace1"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=1.0)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace2"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=0.000001)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace3"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(1.0)])
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace4"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=1, rules=[SamplingRule(0)])
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace5"):
        with tracer.trace("child"):
            pass

    sampler = DatadogSampler(default_sample_rate=1)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace6"):
        with tracer.trace("child") as span:
            span.set_tag(MANUAL_DROP_KEY)

    sampler = DatadogSampler(default_sample_rate=1)
    tracer.configure(sampler=sampler, writer=writer)
    with tracer.trace("trace7"):
        with tracer.trace("child") as span:
            span.set_tag(MANUAL_KEEP_KEY)

    sampler = RateSampler(0.0000000001)
    tracer.configure(sampler=sampler, writer=writer)
    # This trace should not appear in the snapshot
    with tracer.trace("trace8"):
        with tracer.trace("child"):
            pass

    tracer.shutdown()


# Have to use sync mode snapshot so that the traces are associated to this
# test case since we use a custom writer (that doesn't have the trace headers
# injected).
@snapshot(async_mode=False)
def test_synchronous_writer():
    tracer = Tracer()
    writer = AgentWriter(tracer.writer.agent_url, sync_mode=True, priority_sampler=tracer.priority_sampler)
    tracer.configure(writer=writer)
    with tracer.trace("operation1", service="my-svc"):
        with tracer.trace("child1"):
            pass

    with tracer.trace("operation2", service="my-svc"):
        with tracer.trace("child2"):
            pass
