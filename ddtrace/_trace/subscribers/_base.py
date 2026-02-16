from types import TracebackType
from typing import Any
from typing import Generic
from typing import Optional
from typing import TypeVar

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace._trace.trace_handlers import _finish_span
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.events import TracingEvent
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.trace import tracer


TracingEventType = TypeVar("TracingEventType", bound=TracingEvent)


def _start_span(ctx: core.ExecutionContext[TracingEventType]) -> Span:
    """Adaptation of _start_span from trace_handlers to use the event directly

    Args:
        ctx: ExecutionContext containing the event
    Returns:
        The created Span
    """
    event = ctx.event

    activate_distributed_headers = ctx.get_item("activate_distributed_headers")
    integration_config = ctx.get_item("integration_config")
    if integration_config and activate_distributed_headers:
        trace_utils.activate_distributed_headers(
            tracer,
            int_config=integration_config,
            request_headers=ctx.get_item("distributed_headers"),
            override=ctx.get_item("distributed_headers_config_override"),
        )

    span_kwargs: dict[str, Any] = {
        "span_type": event.span_type,
        "resource": event.resource,
        "service": event.service,
    }

    if event.distributed_context and not event.activate:
        span_kwargs["child_of"] = event.distributed_context

    if config._inferred_proxy_services_enabled:
        # TODO(IDM): Subscriber should be added for Inferred Proxy span handling
        # dispatch event for checking headers and possibly making an inferred proxy span
        core.dispatch("inferred_proxy.start", (ctx, span_kwargs, event.activate))
        # re-get span_kwargs in case an inferred span was created and we have a new span_kwargs.child_of field
        span_kwargs = ctx.get_item("span_kwargs", span_kwargs)

    span = (tracer.trace if event.activate else tracer.start_span)(event.span_name, **span_kwargs)

    span._meta.update({COMPONENT: event.component, SPAN_KIND: event.span_kind, **event.tags})

    if event.measured:
        span.set_metric(_SPAN_MEASURED_KEY, 1)

    ctx.span = span

    if config._inferred_proxy_services_enabled:
        # TODO(IDM): Subscriber should be added for Inferred Proxy span handling
        # dispatch event for inferred proxy finish
        core.dispatch("inferred_proxy.finish", (ctx,))

    return span


class TracingSubscriber(ContextSubscriber[TracingEventType], Generic[TracingEventType]):
    """Subscriber that automatically manages span lifecycle for SpanContextEvent.

    This base class handles span creation and finishing, so subclasses only need to
    override on_started/on_ended for their specific logic.

    Example:
        class MySpanSubscriber(SpanTracingSubscriber):
            event_name = "my.span"

            @classmethod
            def on_started(cls, ctx):
                ctx.span.set_tag("custom.tag", "value")

            @classmethod
            def on_ended(cls, ctx, exc_info):
                if exc_info[1]:
                    ctx.span.set_tag("error", True)

    Attributes:
        _end_span: If False, span won't be finished automatically (defaults to True)
    """

    _end_span = True

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext[TracingEventType]) -> None:
        _start_span(ctx)
        for handler in cls._started_handlers:
            handler(ctx)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[TracingEventType],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        try:
            for handler in cls._ended_handlers:
                handler(ctx, exc_info)
        finally:
            if cls._end_span:
                _finish_span(ctx, exc_info)
