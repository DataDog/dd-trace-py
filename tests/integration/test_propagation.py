import pytest

from ddtrace import Tracer
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.propagation.http import HTTPPropagator
from tests.utils import override_global_config

from .test_integration import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@pytest.fixture(
    params=[
        dict(global_config=dict()),
        dict(
            global_config=dict(_x_datadog_tags_max_length="0", _x_datadog_tags_enabled=False),
        ),
        dict(global_config=dict(), partial_flush_enabled=True, partial_flush_min_spans=2),
    ]
)
def tracer(request):
    global_config = request.param.get("global_config", dict())
    partial_flush_enabled = request.param.get("partial_flush_enabled")
    partial_flush_min_spans = request.param.get("partial_flush_min_spans")
    with override_global_config(global_config):
        tracer = Tracer()
        kwargs = dict()
        if partial_flush_enabled:
            kwargs["partial_flush_enabled"] = partial_flush_enabled
        if partial_flush_min_spans:
            kwargs["partial_flush_min_spans"] = partial_flush_min_spans
        tracer.configure(**kwargs)
        yield tracer
        tracer.shutdown()


@pytest.mark.snapshot()
def test_trace_tags_multispan(tracer):
    # type: (Tracer) -> None
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


@pytest.fixture
def downstream_tracer():
    tracer = Tracer()
    yield tracer
    tracer.shutdown()


@pytest.mark.snapshot()
def test_sampling_decision_downstream(downstream_tracer):
    headers = {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-sampling-priority": "1",
        "x-datadog-tags": "_dd.p.dm=-1",
    }
    context = HTTPPropagator.extract(headers)
    downstream_tracer.context_provider.activate(context)

    with downstream_tracer.trace("p", service="downstream") as span:
        span.set_tag(MANUAL_DROP_KEY)
