import typing as t

from ddtrace.internal import core


if t.TYPE_CHECKING:
    from ddtrace._trace.span import Span  # noqa:F401


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
