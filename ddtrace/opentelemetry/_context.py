"""
Implementation details of parenting open telemetry spans should be kept internal. This will give us the flexibility
to support new features (ex: baggage) and refactor this module with out introducing a breaking change.
"""
from opentelemetry.context.context import Context as OtelContext
from opentelemetry.trace import NonRecordingSpan as OtelNonRecordingSpan
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import SpanContext as OtelSpanContext
import opentelemetry.version

from ddtrace import tracer as ddtracer
from ddtrace.context import Context as DDContext
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.version import parse_version
from ddtrace.opentelemetry._span import Span
from ddtrace.provider import BaseContextProvider as DDBaseContextProvider
from ddtrace.span import Span as DDSpan


# opentelemetry.trace.use_span() uses SPAN_KEY to store active spans to a Context Dictionary.
# This context dictionary is then used to activate and get the current span.
# To fully support the otel api we must use SPAN_KEY to add otel spans to otel context objects.
# https://github.com/open-telemetry/opentelemetry-python/blob/v1.15.0/opentelemetry-api/src/opentelemetry/trace/__init__.py#L571
if parse_version(opentelemetry.version.__version__) >= (1, 4):
    from opentelemetry.trace.propagation import _SPAN_KEY
else:
    # opentelemetry-api<1.4 uses SPAN_KEY instead of _SPAN_KEY
    from opentelemetry.trace.propagation import SPAN_KEY as _SPAN_KEY

log = get_logger(__name__)


class DDRuntimeContext:
    def attach(self, otel_context):
        # type: (OtelContext) -> object
        """
        Activates an OpenTelemetry Span by storing its corresponding Datadog Span/Context in the
        Datadog Context Provider.
        """
        # Get Otel Span from the context object. Otelspan can be none if the context
        # only contains baggage or some other propagated object.
        otel_span = otel_context.get(_SPAN_KEY, None)
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
                    "github issue at: https://github.com/Datadog/dd-trace-py and avoid "
                    "setting the ddtrace OpenTelemetry TracerProvider.",
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
        if isinstance(ddactive, DDSpan):
            return OtelContext({_SPAN_KEY: Span(ddactive)})
        elif isinstance(ddactive, DDContext):
            otel_span_context = OtelSpanContext(ddactive.trace_id or 0, ddactive.span_id or 0, True)
            return OtelContext({_SPAN_KEY: OtelNonRecordingSpan(otel_span_context)})
        else:
            return OtelContext()

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
