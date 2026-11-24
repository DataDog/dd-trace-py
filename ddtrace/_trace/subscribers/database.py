from collections.abc import Callable
from types import TracebackType
from typing import Optional

from ddtrace._trace.events import DatabaseOperationEvent
from ddtrace._trace.subscribers._base import TracingSubscriber
from ddtrace.ext import db
from ddtrace.internal import core
from ddtrace.internal.span_bus import find_span_on_context
from ddtrace.internal.span_bus import span_from_context
from ddtrace.trace import Span


class DatabaseTracingSubscriber(TracingSubscriber[DatabaseOperationEvent]):
    """Apply connection and result metadata around the shared span lifecycle."""

    event_names = (DatabaseOperationEvent.event_name,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[DatabaseOperationEvent]) -> None:
        span = span_from_context(ctx)
        span.set_tags(ctx.event.connection_tags)
        span.set_tags(ctx.event.tags)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[DatabaseOperationEvent],
        exc_info: tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        _apply_database_metadata(ctx)


def _apply_database_metadata(ctx: core.ExecutionContext[DatabaseOperationEvent]) -> None:
    span = find_span_on_context(ctx)
    if span is None:
        return
    if ctx.event.connection_tags:
        span.set_tags(ctx.event.connection_tags)
        span.set_tags(ctx.event.tags)
        ctx.event.connection_tags = {}
    result = ctx.event.result
    ctx.event.result = None
    if result is not None:
        if result.row_count is not None:
            span._set_attribute(db.ROWCOUNT, result.row_count)
        if result.row_number is not None:
            span._set_attribute("db.rownumber", result.row_number)
        span.set_tags(result.tags)


def _run_legacy_post_execute(ctx: core.ExecutionContext[DatabaseOperationEvent], hook: Callable[[Span], None]) -> None:
    """Keep span-taking cursor extensions behind the tracing compatibility path."""
    span = find_span_on_context(ctx)
    if span is None:
        return
    _apply_database_metadata(ctx)
    try:
        hook(span)
    finally:
        ctx.set_item("service", span.service)
