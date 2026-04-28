from types import TracebackType
from typing import Any
from typing import ClassVar
from typing import Generic
from typing import Optional
from typing import Sequence
from typing import TypeVar

from ddtrace import config
from ddtrace._trace.events import TracingEvent
from ddtrace._trace.events import TracingEvents
from ddtrace._trace.span import Span
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.trace import tracer


TracingEventType = TypeVar("TracingEventType", bound=TracingEvent)


def _finish_span(
    ctx: core.ExecutionContext[TracingEventType],
    exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
) -> None:
    """Finish the span in the context.

    If no span is present, do nothing.
    Reimplementing finish span here prevents circular import with trace_handlers.
    Once every integration adopts the events API, trace_handlers._finish_span
    should be completely removed.
    """
    span = ctx.span
    if not span:
        return

    set_service_and_source(span, ctx.get_item("service"), ctx.event.integration_config or dict())

    exc_type, exc_value, exc_traceback = exc_info
    if exc_type and exc_value and exc_traceback:
        span.set_exc_info(exc_type, exc_value, exc_traceback)
    elif ctx.get_item("should_set_traceback", False):
        span.set_traceback()
    span.finish()


def _start_span(ctx: core.ExecutionContext[TracingEventType]) -> Span:
    """Adaptation of _start_span from trace_handlers to use the event directly
    Once every integration adopted events API, trace_handlers _start_span
    should be completely removed.

    Args:
        ctx: ExecutionContext containing the event
    Returns:
        The created Span
    """
    event = ctx.event

    activate_distributed_headers = event.activate_distributed_headers
    integration_config = event.integration_config
    if integration_config and activate_distributed_headers:
        trace_utils.activate_distributed_headers(
            tracer,
            int_config=integration_config,
            request_headers=getattr(event, "request_headers", None),
            override=getattr(event, "distributed_headers_config_override", None),
        )

    span_kwargs: dict[str, Any] = {
        "span_type": event.span_type,
        "resource": event.resource,
        "service": event.service,
        "activate": event.activate,
    }

    if config._inferred_proxy_services_enabled:
        # TODO(IDM): Subscriber should be added for Inferred Proxy span handling
        # dispatch event for checking headers and possibly making an inferred proxy span
        core.dispatch("inferred_proxy.start", (ctx, span_kwargs, event.use_active_context))
        # re-get span_kwargs in case an inferred span was created and we have a new span_kwargs.child_of field
        span_kwargs = ctx.get_item("span_kwargs", span_kwargs)

    default_child_of = tracer.context_provider.active() if event.use_active_context else event.distributed_context
    if default_child_of is not None:
        span_kwargs.setdefault("child_of", default_child_of)

    span = tracer.start_span(event.operation_name, **span_kwargs)
    span._set_attribute(COMPONENT, event.component)
    span._set_attribute(SPAN_KIND, event.span_kind)
    for _k, _v in event.tags.items():
        span._set_attribute(_k, _v)
    if event.metrics:
        span.set_metrics(event.metrics)

    if event.measured:
        span._set_attribute(_SPAN_MEASURED_KEY, 1)

    set_service_and_source(span, ctx.get_item("service"), integration_config or dict())
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
            event_names = ("my.span",)

            @classmethod
            def on_started(cls, ctx):
                ctx.span.set_tag("custom.tag", "value")

            @classmethod
            def on_ended(cls, ctx, exc_info):
                if exc_info[1]:
                    ctx.span.set_tag("error", True)
    """

    # Register here events that just create / finish spans
    event_names: ClassVar[Sequence[str]] = (TracingEvents.SPAN_LIFECYCLE.value,)

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
            if getattr(ctx.event, "_end_span", True):
                _finish_span(ctx, exc_info)
