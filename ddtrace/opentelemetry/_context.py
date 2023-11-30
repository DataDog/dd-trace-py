"""
Implementation details of parenting open telemetry spans should be kept internal. This will give us the flexibility
to support new features (ex: baggage) and refactor this module with out introducing a breaking change.
"""
from opentelemetry.context.context import Context as OtelContext
from opentelemetry.trace import NonRecordingSpan as OtelNonRecordingSpan
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import SpanContext as OtelSpanContext
from opentelemetry.trace import get_current_span
from opentelemetry.trace import set_span_in_context

from ddtrace import tracer as ddtracer
from ddtrace.context import Context as DDContext
from ddtrace.internal.logger import get_logger
from ddtrace.opentelemetry._span import Span
from ddtrace.provider import BaseContextProvider as DDBaseContextProvider  # noqa:F401
from ddtrace.span import Span as DDSpan


log = get_logger(__name__)


class DDRuntimeContext:
    def attach(self, otel_context):
        # type: (OtelContext) -> object
        """
        Converts an OpenTelemetry Span to a Datadog Span/Context then stores the
        Datadog representation in the Global DDtrace Trace Context Provider.
        """
        otel_span = get_current_span(otel_context)
        if otel_span:
            if isinstance(otel_span, Span):
                self._ddcontext_provider.activate(otel_span._ddspan)
            elif isinstance(otel_span, OtelSpan):
                trace_id, span_id, *_ = otel_span.get_span_context()
                ddcontext = DDContext(trace_id, span_id)
                self._ddcontext_provider.activate(ddcontext)
            else:
                log.error(
                    "Programming ERROR: ddtrace does not support activiting spans with the type: %s. Please open a "
                    "github issue at: https://github.com/Datadog/dd-trace-py and set DD_TRACE_OTEL_ENABLED=True.",
                    type(otel_span),
                )

        # A return value with the type `object` is required by the otel api to remove/deactivate spans.
        # Since manually deactivating spans is not supported by ddtrace this object will never be used.
        return object()

    def get_current(self):
        # type: (...) -> OtelContext
        """
        Converts the active datadog span to an Opentelemetry Span and then stores it
        in a format that can be parsed by the OpenTelemetry API.
        """
        ddactive = self._ddcontext_provider.active()
        context = OtelContext()
        if isinstance(ddactive, DDSpan):
            span = Span(ddactive)
            context = set_span_in_context(span, context)
        elif isinstance(ddactive, DDContext):
            span_context = OtelSpanContext(ddactive.trace_id or 0, ddactive.span_id or 0, True)
            span = OtelNonRecordingSpan(span_context)
            context = set_span_in_context(span, context)
        return context

    def detach(self, token):
        # type: (object) -> None
        """
        NOP, The otel api uses this method to deactivate spans but this operation is not supported by
        the datadog context provider.
        """
        pass

    @property
    def _ddcontext_provider(self):
        # type: () -> DDBaseContextProvider
        """
        Get the ddtrace context provider from the global Datadog tracer.
        This can reterive a default, gevent, or asyncio context provider.
        """
        return ddtracer.context_provider
