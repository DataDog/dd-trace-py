from ddtrace.propagation.http import _B3MultiHeader
from ddtrace.propagation.http import _DatadogMultiHeader
from ddtrace.propagation.http import _TraceContext


class CorePropagator:
    def __init__(self, propagator):
        # Iniline imports to avoid circular dependencies
        self.dd_propagator = propagator

    def extract(self, carrier, context, getter):
        from opentelemetry.context.context import Context as OtelContext
        from opentelemetry.trace import NonRecordingSpan as OtelNonRecordingSpan
        from opentelemetry.trace import SpanContext as OtelSpanContext
        from opentelemetry.trace import set_span_in_context
        from opentelemetry.trace.span import TraceFlags
        from opentelemetry.trace.span import TraceState

        if context is None:
            context = OtelContext()

        headers = {key: getter.get(carrier, key)[0] for key in carrier.keys()}
        dd_context = self.dd_propagator._extract(headers)
        if not dd_context:
            return context

        tf = TraceFlags.SAMPLED if dd_context._traceflags == "01" else TraceFlags.DEFAULT
        ts = TraceState.from_header([dd_context._tracestate])
        span_context = OtelSpanContext(dd_context.trace_id or 0, dd_context.span_id or 0, True, tf, ts)
        span = OtelNonRecordingSpan(span_context)
        return set_span_in_context(span, context)

    def inject(self, carrier, context, setter):
        """Extracts context from a carrier and sets it in a context object."""
        from opentelemetry.trace import Span as OtelSpan
        from opentelemetry.trace import get_current_span

        from .span import Span

        otel_span = get_current_span(context)
        dd_context = None
        if otel_span:
            if isinstance(otel_span, Span):
                dd_context = otel_span._ddspan.context
            elif isinstance(otel_span, OtelSpan):
                trace_id, span_id, _, tf, ts, _ = otel_span.get_span_context()
                trace_state = ts.to_header() if ts else None
                dd_context = _TraceContext._get_context(trace_id, span_id, tf, trace_state)

        dd_carrier = {}
        if dd_context:
            self.dd_propagator._inject(dd_context, dd_carrier)
            for key, value in dd_carrier.items():
                setter.set(carrier, key, value)
        return carrier


class DatadogPropagator(CorePropagator):
    def __init__(self):
        super().__init__(_DatadogMultiHeader())


class B3Propagator(CorePropagator):
    def __init__(self):
        super().__init__(_B3MultiHeader())
