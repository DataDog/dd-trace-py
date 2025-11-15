from ddtrace._trace.context import Context as DDContext
from ddtrace._trace.provider import BaseContextProvider as DDBaseContextProvider  # noqa:F401
from ddtrace._trace.span import Span as DDSpan
from ddtrace.internal.logger import get_logger
from ddtrace.trace import tracer as ddtracer


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
        from opentelemetry.baggage import get_all
        from opentelemetry.trace import INVALID_SPAN
        from opentelemetry.trace import get_current_span

        from .span import Span
        from .trace import _otel_to_dd_span_context

        otel_span = get_current_span(otel_context)
        if isinstance(otel_span, Span):
            self._ddcontext_provider.activate(otel_span._ddspan)
            ddcontext = otel_span._ddspan.context
        elif otel_span is not INVALID_SPAN:
            ddcontext = _otel_to_dd_span_context(otel_span)
            self._ddcontext_provider.activate(ddcontext)
        else:
            return object()

        # get current open telemetry baggage and store it on the datadog context object
        # fix: we need to support setting baggage when there is no active span
        otel_baggage = get_all(otel_context)
        if ddcontext:
            ddcontext.remove_all_baggage_items()
            if otel_baggage:
                for key, value in otel_baggage.items():
                    ddcontext._baggage[key] = value  # potentially convert to json

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
        from opentelemetry.trace.span import TraceState

        from .span import Span
        from .span import _get_trace_flags

        ddactive = self._ddcontext_provider.active()
        context = OtelContext()
        if isinstance(ddactive, DDSpan):
            span = Span(ddactive)
            context = set_span_in_context(span, context)
        elif isinstance(ddactive, DDContext):
            ts = TraceState.from_header([ddactive._tracestate])
            tf = _get_trace_flags(ddactive.sampling_priority)
            sc = OtelSpanContext(ddactive.trace_id or 0, ddactive.span_id or 0, ddactive._is_remote, tf, ts)
            span = OtelNonRecordingSpan(sc)
            context = set_span_in_context(span, context)

        if isinstance(ddactive, DDSpan):
            dd_baggage = ddactive.context._baggage
        elif isinstance(ddactive, DDContext):
            dd_baggage = ddactive._baggage
        else:
            dd_baggage = {}

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
