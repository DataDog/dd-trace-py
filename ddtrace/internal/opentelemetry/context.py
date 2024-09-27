from ddtrace import tracer as ddtracer
from ddtrace._trace.context import Context as DDContext
from ddtrace._trace.provider import BaseContextProvider as DDBaseContextProvider  # noqa:F401
from ddtrace._trace.span import Span as DDSpan
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import _TraceContext


log = get_logger(__name__)


class DDRuntimeContext:
    """
    Responsible for converting between OpenTelemetry and Datadog spans. This class is loaded by entrypoint
    when `opentelemetry.context` is imported.
    """

    def attach(self, otel_context):
        """
        Converts an OpenTelemetry Span to a Datadog Span/Context then stores the
        Datadog representation in the Global DDtrace Trace Context Provider.
        """
        # Inline opentelemetry imports to avoid circular imports.
        from opentelemetry.baggage import get_all as get_all_baggage
        from opentelemetry.trace import Span as OtelSpan
        from opentelemetry.trace import get_current_span

        from .span import Span

        ddcontext = None
        otel_span = get_current_span(otel_context)
        if otel_span:
            if isinstance(otel_span, Span):
                self._ddcontext_provider.activate(otel_span._ddspan)
                ddcontext = otel_span._ddspan.context
            elif isinstance(otel_span, OtelSpan):
                trace_id, span_id, _, tf, ts, _ = otel_span.get_span_context()
                trace_state = ts.to_header() if ts else None
                ddcontext = _TraceContext._get_context(trace_id, span_id, tf, trace_state)
                self._ddcontext_provider.activate(ddcontext)
            else:
                ddcontext = None
                log.error(
                    "Programming ERROR: ddtrace does not support activiting spans with the type: %s. Please open a "
                    "github issue at: https://github.com/Datadog/dd-trace-py and set DD_TRACE_OTEL_ENABLED=True.",
                    type(otel_span),
                )

        # get current open telemetry baggage and store it on the datadog context object
        otel_baggage = get_all_baggage(otel_context)
        if otel_baggage:
            if not ddcontext:
                ddcontext = DDContext()
                self._ddcontext_provider.activate(ddcontext)
            for key, value in otel_baggage.items():  # type: ignore
                ddcontext._set_baggage_item(key, value)
                # ddcontext._baggage[key] = value  # potentially convert to json

        # A return value with the type `object` is required by the otel api to remove/deactivate spans.
        # Since manually deactivating spans is not supported by ddtrace this object will never be used.
        return object()

    def get_current(self):
        """
        Converts the active datadog span to an Opentelemetry Span and then stores it
        in a format that can be parsed by the OpenTelemetry API.
        """
        # Inline opentelemetry imports to avoid circular imports.
        from opentelemetry.baggage import set_baggage
        from opentelemetry.context.context import Context as OtelContext
        from opentelemetry.trace import NonRecordingSpan as OtelNonRecordingSpan
        from opentelemetry.trace import SpanContext as OtelSpanContext
        from opentelemetry.trace import set_span_in_context
        from opentelemetry.trace.span import TraceFlags
        from opentelemetry.trace.span import TraceState

        from .span import Span

        # getting current active span
        ddactive = self._ddcontext_provider.active()
        context = OtelContext()  # store current datadog baggage into here
        if isinstance(ddactive, DDSpan):
            span = Span(ddactive)
            context = set_span_in_context(span, context)
        elif isinstance(ddactive, DDContext):
            tf = TraceFlags.SAMPLED if ddactive._traceflags == "01" else TraceFlags.DEFAULT
            ts = TraceState.from_header([ddactive._tracestate])
            span_context = OtelSpanContext(ddactive.trace_id or 0, ddactive.span_id or 0, True, tf, ts)
            span = OtelNonRecordingSpan(span_context)
            context = set_span_in_context(span, context)

        if isinstance(ddactive, DDSpan):
            dd_baggage = ddactive.context._baggage
        elif isinstance(ddactive, DDContext):
            dd_baggage = ddactive._baggage
        else:
            dd_baggage = {}

        # getting current active baggage
        for key, value in dd_baggage.items():
            context = set_baggage(key, value, context)

        return context

    def detach(self, token):
        """
        NOP, The otel api uses this method to deactivate spans but this operation is not supported by
        the datadog context provider.
        """
        pass

    @property
    def _ddcontext_provider(self):
        """
        Get the ddtrace context provider from the global Datadog tracer.
        This can reterive a default, gevent, or asyncio context provider.
        """
        return ddtracer.context_provider
