import pytest

import ddtrace
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.propagation.http import HTTPPropagator

from .test_integration import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    env=dict(
        DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH=["512", "0"],
        DD_TRACE_X_DATADOG_TAGS_ENABLED=["1", "0"],
        DD_TRACE_PARTIAL_FLUSH_ENABLED=["1", "0"],
        DD_TRACE_PARTIAL_FLUSH_MIN_SPANS=["2", "0"],
    )
)
def test_trace_tags_multispan(tracer):
    # type: (ddtrace.Tracer) -> None
    headers = {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-sampling-priority": "1",
        "x-datadog-tags": "_dd.p.dm=-1,_dd.p.test=value,any=tag",
    }
    context = HTTPPropagator.extract(headers)
    # DEV: Trace consists of a simple p->c1 case where c1 is finished before p.
    # But the trace also includes p->c2->gc where c2 and p are finished before
    # gc is finished.
    p = tracer.start_span("p", child_of=context)
    c1 = tracer.start_span("c1", child_of=p)
    c1.finish()
    c2 = tracer.start_span("c2", child_of=p)
    gc = tracer.start_span("gc", child_of=c2)
    c2.finish()
    # If partial flushing had been enabled, this will send [c1, c2] as a trace chunk to the agent.
    tracer.flush()
    p.finish()
    gc.finish()


@pytest.mark.snapshot()
def test_sampling_decision_downstream():
    """
    Ensures that set_tag(MANUAL_DROP_KEY) on a span causes the sampling decision meta and sampling priority metric
    to be set appropriately indicating rejection
    """
    headers_indicating_kept_trace = {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-sampling-priority": "1",
        "x-datadog-tags": "_dd.p.dm=-1",
    }
    kept_trace_context = HTTPPropagator.extract(headers_indicating_kept_trace)
    downstream_tracer = ddtrace.tracer
    downstream_tracer.context_provider.activate(kept_trace_context)

    with downstream_tracer.trace("p", service="downstream") as span_to_reject:
        span_to_reject.set_tag(MANUAL_DROP_KEY)
