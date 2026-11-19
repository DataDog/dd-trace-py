from types import TracebackType
from typing import Any
from typing import Optional
from typing import cast

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.dbapi import DatabaseOperationEvent
from ddtrace.contrib._events.dbapi import DatabaseResultEvent
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import db
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.span_bus import find_span_on_context
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.span_bus import store_span_on_context
from ddtrace.trace import tracer


class DatabaseTracingSubscriber(ContextSubscriber[DatabaseOperationEvent]):
    """Own database spans while preserving driver-specific lifecycle conventions."""

    auto_register = False
    event_names = (DatabaseOperationEvent.event_name,)

    @classmethod
    def register(cls) -> None:
        super().register()
        core.on(DatabaseResultEvent.event_name, cls.on_result, name=f"{cls.__name__}.result")

    @classmethod
    def unregister(cls) -> None:
        core.reset_listeners(DatabaseResultEvent.event_name, cls.on_result)
        super().unregister()

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[DatabaseOperationEvent]) -> None:
        event = ctx.event
        span = tracer.trace(
            event.operation_name,
            service=None if event.service_from_config else event.service,
            resource=event.resource,
            span_type=event.span_type,
        )
        store_span_on_context(ctx, span)
        if event.service_from_config:
            set_service_and_source(span, event.service or "", event.integration_config)
        if event.measured:
            span._set_attribute(_SPAN_MEASURED_KEY, 1)
        if event.metadata_before_tags:
            span._set_attribute(COMPONENT, event.component)
            span._set_attribute(SPAN_KIND, event.span_kind)
        span.set_tags(event.get_connection_tags())
        span.set_tags(event.tags)
        if not event.metadata_before_tags:
            span._set_attribute(COMPONENT, event.component)
            span._set_attribute(SPAN_KIND, event.span_kind)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[DatabaseOperationEvent],
        exc_info: tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        span = span_from_context(ctx)
        try:
            if ctx.event.connection_tags_updated:
                span.set_tags(ctx.event.get_connection_tags())
                span.set_tags(ctx.event.tags)
        finally:
            span.__exit__(*exc_info)

    @classmethod
    def on_result(cls, event: DatabaseResultEvent) -> None:
        span = find_span_on_context(event.context)
        if span is None:
            return
        if event.legacy_post_execute is not None:
            event.legacy_post_execute(span)
            return
        if event.row_count is not None or not event.skip_none_row_count:
            span._set_attribute(db.ROWCOUNT, event.row_count)
            if event.row_count_as_tag and isinstance(event.row_count, int) and event.row_count >= 0:
                span.set_tag(db.ROWCOUNT, cast(Any, event.row_count))
        if event.row_number is not None:
            span._set_attribute("db.rownumber", event.row_number)
        span.set_tags(event.tags)
