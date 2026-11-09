"""Flask tracing phases over the shared deferred web request lifecycle."""

from types import TracebackType
from typing import Optional

from ddtrace._trace.subscribers._base import SpanLifecycleSubscriber
from ddtrace._trace.subscribers.web_framework import WebFrameworkRequestSubscriberBase
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.span_bus import span_from_context


class FlaskRequestSubscriber(SpanLifecycleSubscriber[WebFrameworkRequestEvent], WebFrameworkRequestSubscriberBase):
    auto_register = False
    event_names = ("flask.request",)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[WebFrameworkRequestEvent]) -> None:
        span = span_from_context(ctx)
        # Request preparation supplies these tags; an early security block can bypass it.
        span._remove_attribute(COMPONENT)
        span._remove_attribute(SPAN_KIND)
        span._ignore_exception(StopIteration)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # Flask's metadata/security phase hooks already applied the final HTTP data.
        # Reapplying it here would overwrite raw URLs and application resource changes.
        span = span_from_context(ctx)
        for key, value in ctx.event.additional_tags.items():
            span._set_attribute(key, value)
