import typing as t


try:
    from ddtrace.trace import Span
    from ddtrace.trace import TraceFilter
except ImportError:
    # ddtrace 2.x compatibility
    from ddtrace import Span  # type: ignore[attr-defined, no-redef]
    from ddtrace.filters import TraceFilter  # type: ignore[import-not-found, no-redef]

from ddtestpy.internal.utils import DDTESTOPT_ROOT_SPAN_RESOURCE
from ddtestpy.internal.writer import Event
from ddtestpy.internal.writer import TestOptWriter


class TestOptSpanProcessor(TraceFilter):
    def __init__(self, writer: TestOptWriter) -> None:
        self.writer = writer

    def process_trace(self, trace: t.List[Span]) -> t.Optional[t.List[Span]]:
        for span in trace:
            if span.parent_id is None:
                continue
            if span.resource == DDTESTOPT_ROOT_SPAN_RESOURCE:
                continue

            event = span_to_event(span)
            self.writer.put_event(event)
        return None


def span_to_event(span: Span) -> Event:
    metrics = span.get_metrics()
    metrics.pop("_dd.top_level", None)

    return Event(
        version=1,
        type="span",
        content={
            "trace_id": span.trace_id % (1 << 64),
            "parent_id": span.parent_id % (1 << 64) if span.parent_id is not None else None,
            "span_id": span.span_id % (1 << 64),
            "service": span.service,
            "resource": span.resource,
            "name": span.name,
            "error": span.error,
            "start": span.start_ns,
            "duration": span.duration_ns,
            "meta": span.get_tags(),
            "metrics": metrics,
            "type": span.get_tag("type") or span.span_type,
        },
    )
