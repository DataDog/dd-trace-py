from opentelemetry.context.context import Context as OtelContext
from opentelemetry.trace import NonRecordingSpan as OtelNonRecordingSpan
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import SpanContext as OtelSpanContext
import opentelemetry.version

from ddtrace.internal.utils.version import parse_version


if parse_version(opentelemetry.version.__version__) < (1, 4):
    # In opentelemetry.trace.use_span() SPAN_KEY is used to add Spans to a Context Dictionary.
    # This context dictionary is then used to activate and get the current span.
    # To fully support the otel api we must use SPAN_KEY to add otel spans to otel context objects.
    # https://github.com/open-telemetry/opentelemetry-python/blob/v1.15.0/opentelemetry-api/src/opentelemetry/trace/__init__.py#L571
    from opentelemetry.trace.propagation import SPAN_KEY as _DDOTEL_SPAN_KEY
else:
    # opentelemetry-api>=1.4 uses _SPAN_KEY to store spans in the Context Dictionary
    from opentelemetry.trace.propagation import _SPAN_KEY as _DDOTEL_SPAN_KEY

from ddtrace.context import Context as DDContext
from ddtrace.internal.utils import get_argument_value
from ddtrace.opentelemetry.span import Span
from ddtrace.provider import DefaultContextProvider
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


_PATCHED_CONTEXT = False

# Used to access the active datadog span.
# DD_CONTEXT_PROVIDER is set to GeventContextProvider when gevent is patched.
DD_CONTEXT_PROVIDER = DefaultContextProvider()


def _dd_runtime_context_attach(wrapped, instance, args, kwargs):
    # type: (...) -> object
    """Gets an otel span from the context object, and activates the corresponding
    datadog span or datadog context object.
    """
    otel_context = get_argument_value(args, kwargs, 0, "context")  # type: OtelContext
    # Get Otel Span from the context object. Otelspan can be none if the context
    # only contains baggage or some other propagated object.
    # Note - _DDOTEL_SPAN_KEY is used by
    otel_span = otel_context.get(_DDOTEL_SPAN_KEY, None)

    if otel_span:
        if isinstance(otel_span, Span):
            DD_CONTEXT_PROVIDER.activate(otel_span._ddspan)
        elif isinstance(otel_span, OtelSpan):
            trace_id, span_id, *_ = otel_span.get_span_context()
            ddcontext = DDContext(trace_id, span_id)
            DD_CONTEXT_PROVIDER.activate(ddcontext)
        else:
            # Update this codeblock to support baggage
            raise ValueError("The following span is not compatible with ddtrace: %s" % (otel_context,))

    return object()


def _dd_runtime_context_get_current(wrapped, instance, args, kwargs):
    # type: (...) -> OtelContext
    """Converts the active datadog span to an Opentelemetry Span and then stores it an OtelContext
    in a format that can be parsed bu the OpenTelemetry API
    """
    ddactive = DD_CONTEXT_PROVIDER.active()
    if ddactive is None:
        return OtelContext()
    elif isinstance(ddactive, DDContext):
        otel_span_context = OtelSpanContext(ddactive.trace_id, ddactive.span_id, True)
        return OtelContext({_DDOTEL_SPAN_KEY: OtelNonRecordingSpan(otel_span_context)})
    else:
        # ddactive is a datadog span, create a new otel span using the active datadog span
        return OtelContext({_DDOTEL_SPAN_KEY: Span(ddactive)})


def _dd_runtime_context_detach(wrapped, instance, args, kwargs):
    # type: (...) -> None
    """NOOP, datadog context provider does not support manual deactivation"""
    pass


def wrap_otel_context():
    # type: () -> None
    """wraps the default Otel Context Manager. Warning ContextVarsRuntimeContext can be overridden by setting"""
    global _PATCHED_CONTEXT
    if _PATCHED_CONTEXT:
        return
    _PATCHED_CONTEXT = True

    _w("opentelemetry.context.contextvars_context", "ContextVarsRuntimeContext.attach", _dd_runtime_context_attach)
    _w(
        "opentelemetry.context.contextvars_context",
        "ContextVarsRuntimeContext.get_current",
        _dd_runtime_context_get_current,
    )
    _w("opentelemetry.context.contextvars_context", "ContextVarsRuntimeContext.detach", _dd_runtime_context_detach)
