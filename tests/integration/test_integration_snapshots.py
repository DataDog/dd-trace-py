# -*- coding: utf-8 -*-
import multiprocessing

import mock
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


@snapshot(async_mode=False)
def test_tracer_trace_across_fork():
    """
    When a trace is started in a parent process and a child process is spawned
        The trace should be continued in the child process
    """
    tracer = Tracer()

    def task(tracer):
        with tracer.trace("child"):
            pass
        tracer.shutdown()

    with tracer.trace("parent"):
        p = multiprocessing.Process(target=task, args=(tracer,))
        p.start()
        p.join()

    tracer.shutdown()


@snapshot(async_mode=False)
def test_tracer_trace_across_multiple_forks():
    """
    When a trace is started and crosses multiple process boundaries
        The trace should be continued in the child processes
    """
    tracer = Tracer()

    def task(tracer):
        def task2(tracer):
            with tracer.trace("child2"):
                pass
            tracer.shutdown()

        with tracer.trace("child1"):
            p = multiprocessing.Process(target=task2, args=(tracer,))
            p.start()
            p.join()
        tracer.shutdown()

    with tracer.trace("parent"):
        p = multiprocessing.Process(target=task, args=(tracer,))
        p.start()
        p.join()
    tracer.shutdown()


@snapshot()
def test_wrong_span_name_type_not_sent():
    """Span names should be a text type."""
    tracer = Tracer()
    with mock.patch("ddtrace.span.log") as log:
        with tracer.trace(123):
            pass
        log.exception.assert_called_once_with("error closing trace")


@pytest.mark.parametrize(
    "meta",
    [
        ({"env": "my-env", "tag1": "some_str_1", "tag2": "some_str_2", "tag3": [1, 2, 3]}),
        ({"env": "test-env", b"tag1": {"wrong_type": True}, b"tag2": "some_str_2", b"tag3": "some_str_3"}),
        ({"env": "my-test-env", u"😐": "some_str_1", b"tag2": "some_str_2", "unicode": 12345}),
    ],
)
@snapshot()
def test_trace_with_wrong_meta_types_not_sent(meta):
    tracer = Tracer()
    with mock.patch("ddtrace.span.log") as log:
        with tracer.trace("root") as root:
            root.meta = meta
            for _ in range(499):
                with tracer.trace("child") as child:
                    child.meta = meta
        log.exception.assert_called_once_with("error closing trace")


@pytest.mark.parametrize(
    "metrics",
    [
        ({"num1": 12345, "num2": 53421, "num3": 1, "num4": "not-a-number"}),
        ({b"num1": 123.45, b"num2": [1, 2, 3], b"num3": 11.0, b"num4": 1.20}),
        ({u"😐": "123.45", b"num2": "1", "num3": {"is_number": False}, "num4": "12345"}),
    ],
)
@snapshot()
def test_trace_with_wrong_metrics_types_not_sent(metrics):
    tracer = Tracer()
    with mock.patch("ddtrace.span.log") as log:
        with tracer.trace("root") as root:
            root.metrics = metrics
            for _ in range(499):
                with tracer.trace("child") as child:
                    child.metrics = metrics
        log.exception.assert_called_once_with("error closing trace")
