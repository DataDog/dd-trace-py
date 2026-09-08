import logging
import typing as t

from ddtrace.internal import core


if t.TYPE_CHECKING:
    from ddtrace._trace.span import Span  # noqa:F401

log = logging.getLogger(__name__)


def get_span() -> t.Optional["Span"]:
    current: t.Optional[core.ExecutionContext[t.Any]] = core.current
    while current is not None:
        if current.get_item("_inner_span") is not None:
            return current["_inner_span"]  # type: ignore
        current = current._parent
    return None


def get_root_span() -> t.Optional["Span"]:
    span = get_span()
    tracer = core.root.get_item("tracer")
    if span is None:
        return None if tracer is None else tracer.current_root_span()
    return span._local_root or span


def span_from_context(ctx: core.ExecutionContext[t.Any]) -> "Span":
    if ctx.get_item("_inner_span") is None:
        log.warning(
            "No span found in %s. "
            "This may indicate the context.started event handler did not set a span. "
            "Creating fallback 'default' span.",
            ctx,
        )
        tracer = ctx.find_item("tracer")
        ctx.set_item("_inner_span", tracer.current_span() or tracer.trace("default"))
    return ctx["_inner_span"]  # type: ignore


def store_span_on_context(ctx: core.ExecutionContext[t.Any], value: "Span") -> None:
    ctx.set_item("_inner_span", value)
    if "span_key" in ctx._data:
        ctx._data[ctx._data["span_key"]] = value
