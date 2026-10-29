"""Flask's deferred request tracing lifetime."""

from types import TracebackType
from typing import Optional

from ddtrace._trace.subscribers._base import SpanLifecycleSubscriber
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.span_bus import span_from_context


class FlaskRequestSubscriber(SpanLifecycleSubscriber[WebFrameworkRequestEvent]):
    auto_register = False
    event_names = ("flask.request",)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[WebFrameworkRequestEvent]) -> None:
        span = span_from_context(ctx)
        # Legacy Flask applies these tags during request preparation, which an
        # early block can bypass. Keep that boundary until those callbacks move.
        span._remove_attribute(COMPONENT)
        span._remove_attribute(SPAN_KIND)
        ctx.set_item("req_span", span)
        span._ignore_exception(StopIteration)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # Existing Flask callbacks already finalized HTTP metadata. Reapplying it
        # here would overwrite user resources and raw URL/header information.
        span = span_from_context(ctx)
        for key, value in ctx.event.additional_tags.items():
            span._set_attribute(key, value)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        response_span = ctx.get_item("resp_span")
        if response_span is None:
            super()._on_context_ended(ctx, exc_info)
            return
        try:
            try:
                if exc_info[0] is not None and not issubclass(exc_info[0], StopIteration):
                    response_span.set_exc_info(*exc_info)
            finally:
                response_span.finish()
        finally:
            # Stream errors belong to the response child. Application/root errors
            # were already recorded before the response iterable was created.
            super()._on_context_ended(ctx, (None, None, None))
