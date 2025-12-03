import traceback
from types import TracebackType
from typing import Optional
from typing import Tuple

from opentelemetry import trace
from opentelemetry.context import attach
from opentelemetry.context import detach
from opentelemetry.trace.status import Status
from opentelemetry.trace.status import StatusCode

from ddtrace.contrib.compat import core


def _start_span(ctx: core.ExecutionContext, call_trace: bool = True, **kwargs):
    """
    Start an OpenTelemetry span based on the context.

    This is an OpenTelemetry-compatible version of the Datadog trace handler.
    It creates spans using the OpenTelemetry API instead of the Datadog tracer.

    Similar to DD's implementation:
    - call_trace=True: Activates the span (like DD's tracer.trace())
    - call_trace=False: Does not activate (like DD's tracer.start_span())
    """
    call_trace = ctx.get_item("call_trace", call_trace)
    tracer = ctx.get_item("tracer") or trace.get_tracer(__name__)

    span_name = ctx.get_item("span_name")
    if not span_name:
        raise ValueError("span_name must be set in the context before starting a span")

    attributes = {}
    tags = ctx.get_item("tags")
    if tags:
        attributes.update(tags)

    if ctx.get_item("measured"):
        attributes["_dd.measured"] = 1

    if "attributes" in kwargs:
        attributes.update(kwargs.pop("attributes"))

    span_kind = kwargs.pop("kind", trace.SpanKind.INTERNAL)
    span = tracer.start_span(name=span_name, attributes=attributes, kind=span_kind, **kwargs)

    if call_trace:
        otel_context = trace.set_span_in_context(span)
        token = attach(otel_context)
        ctx.set_item("_otel_context_token", token)

    ctx._inner_span = span  # type: ignore[assignment]

    return span


def _finish_span(
    ctx: core.ExecutionContext,
    exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
):
    span = ctx._inner_span  # type: ignore[assignment]
    if not span:
        return

    exc_type, exc_value, exc_traceback = exc_info
    if exc_type and exc_value and exc_traceback:
        span.set_status(Status(StatusCode.ERROR, str(exc_value)))  # type: ignore[attr-defined]
        span.set_attribute("error.type", exc_type.__name__)  # type: ignore[attr-defined]
        span.set_attribute("error.message", str(exc_value))  # type: ignore[attr-defined]
        span.set_attribute("error.stack", "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))  # type: ignore[attr-defined]

    span.end()  # type: ignore[attr-defined]

    # Detach context only if we activated it (when call_trace=True)
    token = ctx.get_item("_otel_context_token")
    if token is not None:
        detach(token)


def listen():
    for context_name in ("emoji.emojize",):
        core.on(f"context.started.{context_name}", _start_span)

    for name in ("emoji.emojize",):
        core.on(f"context.ended.{name}", _finish_span)


listen()
