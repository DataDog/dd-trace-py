"""Request notifications using an integration's existing context and span.

Unlike WebFrameworkRequestEvent, these events neither open a scope nor manage its span.
"""

from dataclasses import dataclass

from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import SpanData


@dataclass
class WebRequestEvent(Event):
    """Reuse the same context and span for Ready and Completing."""

    ctx: ExecutionContext[Event]
    span: SpanData


@dataclass
class WebRequestReady(WebRequestEvent):
    """The span is bound and request analysis can start, before application code runs."""

    event_name = "web.request.ready"


@dataclass
class WebRequestCompleting(WebRequestEvent):
    """Attach analysis before the span finishes, including on errors and cancellation.

    Emit at response completion, including deferred iterators. This may run in
    another task or callback; a nested wrapper must not complete its owner.
    """

    event_name = "web.request.completing"
