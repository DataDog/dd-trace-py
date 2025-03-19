import pytest


@pytest.mark.subprocess(env={"OTEL_PROPAGATORS": "datadog,b3,tracecontext,baggage"})
def test_propagators():
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagate import propagators
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    from ddtrace.internal.opentelemetry.propagators import B3Propagator
    from ddtrace.internal.opentelemetry.propagators import DatadogPropagator

    assert len(propagators) == 4
    assert isinstance(propagators[0], DatadogPropagator)
    assert isinstance(propagators[1], B3Propagator)
    assert isinstance(propagators[2], TraceContextTextMapPropagator)
    assert isinstance(propagators[3], W3CBaggagePropagator)


@pytest.mark.subprocess(env={"OTEL_PROPAGATORS": "datadog"})
def test_propagator_extract_datadog():
    from opentelemetry.propagate import extract
    from opentelemetry.trace import get_current_span
    from opentelemetry.trace.span import TraceFlags

    carrier = {
        "x-datadog-trace-id": "20",
        "x-datadog-parent-id": "30",
        "x-datadog-sampling-priority": "1",
    }
    context = extract(carrier)
    span = get_current_span(context)
    span_context = span.get_span_context()

    assert span_context.trace_id == 20
    assert span_context.span_id == 30
    assert span_context.is_remote is True
    assert span_context.trace_flags is TraceFlags.SAMPLED


@pytest.mark.subprocess(env={"OTEL_PROPAGATORS": "datadog"})
def test_propagator_inject_datadog():
    from opentelemetry.propagate import inject
    from opentelemetry.trace import set_span_in_context
    from opentelemetry.trace.span import NonRecordingSpan
    from opentelemetry.trace.span import SpanContext
    from opentelemetry.trace.span import TraceFlags

    span_context = SpanContext(12345, 67890, True, TraceFlags.SAMPLED)
    remote_span = NonRecordingSpan(span_context)

    carrier = {}
    inject(carrier, set_span_in_context(remote_span))
    assert carrier == {
        "x-datadog-trace-id": "12345",
        "x-datadog-parent-id": "67890",
        "x-datadog-sampling-priority": "1",
    }, carrier
