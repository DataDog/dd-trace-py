from ddtrace import tracer as ddtracer
from ddtrace._trace.context import Context as DDContext
from ddtrace._trace.provider import BaseContextProvider as DDBaseContextProvider  # noqa:F401
from ddtrace._trace.span import Span as DDSpan
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import wrap as _w
from ddtrace.propagation.http import _TraceContext


log = get_logger(__name__)


class DDRuntimeContext:
    """
    Responsible for converting between OpenTelemetry and Datadog spans. This class is loaded by entrypoint
    when `opentelemetry.context` is imported.
    """

    def __init__(self) -> None:
        """monkey patching for otel baggage API"""
        from opentelemetry.baggage import clear
        from opentelemetry.baggage import remove_baggage
        from opentelemetry.baggage import set_baggage

        _w(set_baggage, wrap_set_baggage)
        _w(remove_baggage, wrap_remove_baggage)
        _w(clear, wrap_remove_all_baggage)

    def attach(self, otel_context):
        """
        Converts an OpenTelemetry Span to a Datadog Span/Context then stores the
        Datadog representation in the Global DDtrace Trace Context Provider.
        """
        # Inline opentelemetry imports to avoid circular imports.
        from opentelemetry.trace import Span as OtelSpan
        from opentelemetry.trace import get_current_span

        from .span import Span

        otel_span = get_current_span(otel_context)
        if otel_span:
            if isinstance(otel_span, Span):
                self._ddcontext_provider.activate(otel_span._ddspan)
            elif isinstance(otel_span, OtelSpan):
                trace_id, span_id, _, tf, ts, _ = otel_span.get_span_context()
                trace_state = ts.to_header() if ts else None
                ddcontext = _TraceContext._get_context(trace_id, span_id, tf, trace_state)
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
        """
        Converts the active datadog span to an Opentelemetry Span and then stores it
        in a format that can be parsed by the OpenTelemetry API.
        """
        # Inline opentelemetry imports to avoid circular imports.
        from opentelemetry.context.context import Context as OtelContext
        from opentelemetry.trace import NonRecordingSpan as OtelNonRecordingSpan
        from opentelemetry.trace import SpanContext as OtelSpanContext
        from opentelemetry.trace import set_span_in_context
        from opentelemetry.trace.span import TraceFlags
        from opentelemetry.trace.span import TraceState

        from .span import Span

        ddactive = self._ddcontext_provider.active()
        context = OtelContext()
        if isinstance(ddactive, DDSpan):
            span = Span(ddactive)
            context = set_span_in_context(span, context)
        elif isinstance(ddactive, DDContext):
            tf = TraceFlags.SAMPLED if ddactive._traceflags == "01" else TraceFlags.DEFAULT
            ts = TraceState.from_header([ddactive._tracestate])
            span_context = OtelSpanContext(ddactive.trace_id or 0, ddactive.span_id or 0, True, tf, ts)
            span = OtelNonRecordingSpan(span_context)
            context = set_span_in_context(span, context)
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


# monkey patching for otel baggage API
def wrap_set_baggage(func, args, kwargs):
    """
    Set baggage item on the current context.
    """
    context = func(*args, **kwargs)
    name: str = get_argument_value(args, kwargs, 0, "name")  # type: ignore
    value = get_argument_value(args, kwargs, 1, "value")
    # active ddspan and add baggage
    ddcontext = ddtracer.current_trace_context()
    if ddcontext:
        ddcontext.set_baggage_item(name, value)
    return context


def wrap_remove_baggage(func, args, kwargs):
    """
    Remove baggage item from the current context.
    """
    context = func(*args, **kwargs)
    name: str = get_argument_value(args, kwargs, 0, "name")  # type: ignore
    ddcontext = ddtracer.current_trace_context()
    if ddcontext:
        ddcontext.remove_baggage_item(name)
    return context


def wrap_remove_all_baggage(func, args, kwargs):
    """
    Remove all baggage items from the current context.
    """
    context = func(*args, **kwargs)
    ddcontext = ddtracer.current_trace_context()
    if ddcontext:
        ddcontext.remove_all_baggage_items()
    return context
